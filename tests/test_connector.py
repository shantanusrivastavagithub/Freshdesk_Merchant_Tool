"""Offline unit tests using httpx.MockTransport (no network, no real Freshdesk)."""

from __future__ import annotations

import asyncio
import base64
import json
import httpx
import pytest

from freshdesk_connector.client import FreshdeskClient
from freshdesk_connector.config import Settings, normalize_domain
from freshdesk_connector.errors import AuthenticationError, NotFoundError, RateLimitError, ValidationError
from freshdesk_connector.rate_limiter import RateLimiter
from freshdesk_connector.service import FreshdeskService, build_ticket_query

SETTINGS = Settings(base_url="https://acme.freshdesk.com", api_key="secret-key", max_requests_per_minute=600, max_retries=3)


class FakeSleep:
    def __init__(self):
        self.calls: list[float] = []

    async def __call__(self, s: float) -> None:
        self.calls.append(s)


def make(handler, sleep=None):
    sleep = sleep or FakeSleep()
    client = FreshdeskClient(SETTINGS, transport=httpx.MockTransport(handler), sleep=sleep)
    return FreshdeskService(client), sleep


def run(coro):
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("acme", "https://acme.freshdesk.com"),
        ("acme.freshdesk.com", "https://acme.freshdesk.com"),
        ("https://acme.freshdesk.com/", "https://acme.freshdesk.com"),
        ("http://127.0.0.1:8765", "http://127.0.0.1:8765"),
    ],
)
def test_normalize_domain(raw, expected):
    assert normalize_domain(raw) == expected


def test_sends_basic_auth_with_api_key():
    seen = {}

    def handler(req: httpx.Request):
        seen["auth"] = req.headers["authorization"]
        seen["url"] = str(req.url)
        return httpx.Response(200, json={"id": 1, "contact": {"name": "A", "email": "a@x"}})

    svc, _ = make(handler)
    out = run(svc.check_connection())
    assert out["connected"] is True
    assert seen["auth"] == "Basic " + base64.b64encode(b"secret-key:X").decode()
    assert seen["url"] == "https://acme.freshdesk.com/api/v2/agents/me"


def test_401_raises_auth_error_without_retry():
    calls = []

    def handler(req):
        calls.append(1)
        return httpx.Response(401, json={"code": "invalid_credentials"})

    svc, _ = make(handler)
    with pytest.raises(AuthenticationError):
        run(svc.check_connection())
    assert len(calls) == 1


def test_404_maps_to_not_found():
    svc, _ = make(lambda req: httpx.Response(404, json={}))
    with pytest.raises(NotFoundError):
        run(svc.get_ticket(999))


def test_429_honours_retry_after_then_succeeds():
    responses = iter([
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, json={"id": 5, "subject": "Hi", "status": 2, "priority": 3}),
    ])
    svc, sleep = make(lambda req: next(responses))
    out = run(svc.get_ticket(5))
    assert out["status"] == "Open" and out["priority"] == "High"
    assert any(abs(s - 7) < 0.01 for s in sleep.calls)


def test_429_gives_up_after_max_retries():
    svc, sleep = make(lambda req: httpx.Response(429, headers={"Retry-After": "2"}))
    with pytest.raises(RateLimitError) as ei:
        run(svc.get_ticket(1))
    assert ei.value.retry_after == 2


def test_5xx_is_retried_with_backoff():
    responses = iter([
        httpx.Response(503),
        httpx.Response(502),
        httpx.Response(200, json={"id": 1}),
    ])
    svc, sleep = make(lambda req: next(responses))
    assert run(svc.get_contact(1))["id"] == 1
    assert len(sleep.calls) == 2


def test_token_bucket_throttles():
    t = [0.0]
    sleeps = []

    async def fake_sleep(s):
        sleeps.append(s)
        t[0] += s

    rl = RateLimiter(60, clock=lambda: t[0], sleep=fake_sleep)  # 1 req/sec, burst 60

    async def go():
        for _ in range(61):
            await rl.acquire()

    run(go())
    assert len(sleeps) == 1 and abs(sleeps[0] - 1.0) < 1e-6


def test_list_tickets_pagination_and_params():
    seen = {}

    def handler(req: httpx.Request):
        seen.update(dict(req.url.params))
        return httpx.Response(
            200,
            json=[{"id": 1, "status": 3, "priority": 1, "source": 2}],
            headers={"Link": '<https://acme.freshdesk.com/api/v2/tickets?page=2>; rel="next"'},
        )

    svc, _ = make(handler)
    out = run(
        svc.list_tickets(
            requester_email="jane@example.com",
            updated_since="2026-09-01",
            per_page=1,
        )
    )
    assert out["has_more"] is True
    assert out["tickets"][0]["status"] == "Pending" and out["tickets"][0]["source"] == "Portal"
    assert seen["email"] == "jane@example.com"
    assert seen["updated_since"] == "2026-09-01T00:00:00Z"
    assert "requester_id" not in seen  # None params are dropped


def test_build_query_and_search_wraps_in_quotes():
    q = build_ticket_query(
        status="open",
        priority="urgent",
        tag="refund",
        created_from="2026-09-01",
    )
    assert q == "status:2 AND priority:4 AND tag:'refund' AND created_at:>'2026-09-01'"

    seen = {}

    def handler(req):
        seen["query"] = req.url.params["query"]
        return httpx.Response(200, json={"total": 45, "results": [{"id": 3, "status": 2}]})

    svc, _ = make(handler)
    out = run(svc.search_tickets(status="open"))
    assert seen["query"] == '"status:2"'
    assert out["total"] == 45 and out["has_more"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        {"status": "bogus"},
        {"tag": "x' OR status:2"},
        {"created_from": "yesterday"},
    ],
)
def test_search_rejects_bad_input(kwargs):
    with pytest.raises(ValidationError):
        build_ticket_query(**kwargs)


def test_private_notes_hidden_by_default():
    convs = [
        {"id": 1, "incoming": True, "private": False, "body_text": "help"},
        {"id": 2, "incoming": False, "private": True, "body_text": "internal"},
    ]
    svc, _ = make(lambda req: httpx.Response(200, json=convs))
    out = run(svc.list_ticket_conversations(10))
    assert [c["id"] for c in out["conversations"]] == [1]
    assert out["private_notes_hidden"] == 1

    out = run(svc.list_ticket_conversations(10, include_private_notes=True))
    assert len(out["conversations"]) == 2


def test_client_is_read_only():
    assert not any(hasattr(FreshdeskClient, m) for m in ("post", "put", "patch", "delete"))
