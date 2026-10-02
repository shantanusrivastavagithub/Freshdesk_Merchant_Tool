"""A small fake Freshdesk API for local development and demos.

Emulates: Basic-auth API key, /agents/me, /tickets (list + Link pagination), /tickets/{id}, /tickets/{id}/conversations, /search/tickets, /contacts, /contacts/{id}, and per-minute rate limiting with 429 + Retry-After.

Run:
    python mock_server/mock_freshdesk.py (listens on 127.0.0.1:8765)
Then:
    FRESHDESK_DOMAIN=http://127.0.0.1:8765
    FRESHDESK_API_KEY=mock-api-key
"""

from __future__ import annotations

import base64
import os
import re
import time
from collections import deque

import uvicorn
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

API_KEY = os.environ.get("MOCK_API_KEY", "mock-api-key")
RATE_LIMIT = int(os.environ.get("MOCK_RATE_LIMIT", 30))
_hits: deque[float] = deque()  # requests / minute

CONTACTS = [
    {"id": 1001, "name": "Jane Doe", "email": "jane@example.com", "phone": "+91 98000 00001", "company_id": 5001, "active": True},
    {"id": 1002, "name": "Ravi Kumar", "email": "ravi@example.com", "phone": "+91 98000 00002", "company_id": 5001, "active": True},
    {"id": 1003, "name": "Ana Silva", "email": "ana@example.com", "phone": None, "company_id": None, "active": False},
]

TAGS = [["refund"], ["payment-failed"], ["kyc"], [], ["refund", "vip"]]

TICKETS = [
    {
        "id": i,
        "subject": f"Sample issue #{i}",
        "status": [2, 3, 4, 5][i % 4],
        "priority": [1, 2, 3, 4][(i // 4) % 4],
        "source": [1, 2, 3, 7][i % 4],
        "type": ["Question", "Incident", "Problem"][i % 3],
        "requester_id": CONTACTS[i % 3]["id"],
        "responder_id": 9001,
        "group_id": 7001,
        "tags": TAGS[i % 5],
        "company_id": CONTACTS[i % 3]["company_id"],
        "is_escalated": i % 7 == 0,
        "description_text": f"Customer reports problem number {i}.",
        "created_at": f"2026-09-{(i % 28) + 1:02d}T10:00:00Z",
        "updated_at": f"2026-09-{(i % 28) + 1:02d}T12:00:00Z",
        "due_by": f"2026-10-{(i % 28) + 1:02d}T10:00:00Z",
        "fr_due_by": None,
        "cc_emails": [],
        "custom_fields": {},
    }
    for i in range(1, 76)
]


def _err(status: int, desc: str, headers: dict | None = None) -> JSONResponse:
    return JSONResponse({"description": desc}, status_code=status, headers=headers)


def _guard(request: Request) -> JSONResponse | None:
    auth = request.headers.get("authorization", "")
    user = ""
    try:
        user = base64.b64decode(auth.removeprefix("Basic ")).decode().split(":")[0]
    except Exception:
        pass
    if user != API_KEY:
        return _err(401, "You have to be logged in to perform this action.")

    now = time.time()
    while _hits and now - _hits[0] > 60:
        _hits.popleft()
    if len(_hits) >= RATE_LIMIT:
        retry_after = int(60 - (now - _hits[0])) + 1
        return _err(429, "Rate Limit exceeded", {"Retry-After": str(retry_after)})
    _hits.append(now)
    return None


def _rl_headers() -> dict:
    return {
        "X-RateLimit-Total": str(RATE_LIMIT),
        "X-RateLimit-Remaining": str(max(0, RATE_LIMIT - len(_hits))),
    }


def paged(request: Request, items: list) -> JSONResponse:
    page = int(request.query_params.get("page", 1))
    per_page = min(100, int(request.query_params.get("per_page", 30)))
    chunk = items[(page - 1) * per_page : page * per_page]
    headers = _rl_headers()
    if page * per_page < len(items):
        headers["Link"] = f'<{request.url.include_query_params(page=page + 1)}>; rel="next"'
    return JSONResponse(chunk, headers=headers)


async def me(request: Request):
    if r := _guard(request):
        return r
    return JSONResponse(
        {"id": 9001, "contact": {"name": "Mock Agent", "email": "agent@mock.test"}},
        headers=_rl_headers(),
    )


async def list_tickets(request: Request):
    if r := _guard(request):
        return r
    q = request.query_params
    items = TICKETS
    if email := q.get("email"):
        ids = [c["id"] for c in CONTACTS if c["email"] == email]
        items = [t for t in items if t["requester_id"] in ids]
    if rid := q.get("requester_id"):
        items = [t for t in items if t["requester_id"] == int(rid)]
    reverse = q.get("order_type", "desc") == "desc"
    items = sorted(
        items,
        key=lambda t: t.get(q.get("order_by", "created_at")) or "",
        reverse=reverse,
    )
    return paged(request, items)


async def get_ticket(request: Request):
    if r := _guard(request):
        return r
    tid = int(request.path_params["tid"])
    t = next((t for t in TICKETS if t["id"] == tid), None)
    if not t:
        return _err(404, "Not found")
    out = dict(t)
    if "requester" in request.query_params.get("include", ""):
        out["requester"] = next((c for c in CONTACTS if c["id"] == t["requester_id"]), None)
    return JSONResponse(out, headers=_rl_headers())


async def conversations(request: Request):
    if r := _guard(request):
        return r
    tid = int(request.path_params["tid"])
    if not any(t["id"] == tid for t in TICKETS):
        return _err(404, "Not found")
    convs = [
        {
            "id": tid * 10 + 1,
            "incoming": True,
            "private": False,
            "user_id": 1001,
            "from_email": "jane@example.com",
            "body_text": "Any update?",
            "created_at": "2026-09-20T10:00:00Z",
        },
        {
            "id": tid * 10 + 2,
            "incoming": False,
            "private": True,
            "user_id": 9001,
            "body_text": "Internal: escalate to payments team.",
            "created_at": "2026-09-20T11:00:00Z",
        },
        {
            "id": tid * 10 + 3,
            "incoming": False,
            "private": False,
            "user_id": 9001,
            "body_text": "We are looking into it.",
            "created_at": "2026-09-20T12:00:00Z",
        },
    ]
    return paged(request, convs)


async def search_tickets(request: Request):
    if r := _guard(request):
        return r
    query = request.query_params.get("query", "")
    if not (query.startswith('"') and query.endswith('"')):
        return _err(400, "Query must be enclosed in double quotes")

    items = TICKETS
    for field, op, val in re.findall(r"(\w+):([><]?)(?:'([^']+)'|([^\s]+))", query.strip('"')):
        actual_val = val or ''
        if field in ("status", "priority", "agent_id", "group_id"):
            key = "responder_id" if field == "agent_id" else field
            items = [t for t in items if t[key] == int(actual_val)]
        elif field == "tag":
            items = [t for t in items if actual_val in t["tags"]]
        elif field == "type":
            items = [t for t in items if t["type"] == actual_val]
        elif field in ("created_at", "updated_at"):
            items = [
                t for t in items
                if (t[field][:10] >= actual_val if op == ">" else t[field][:10] <= actual_val)
            ]

    page = int(request.query_params.get("page", 1))
    return JSONResponse(
        {"total": len(items), "results": items[(page - 1) * 30 : page * 30]},
        headers=_rl_headers(),
    )


async def list_contacts(request: Request):
    if r := _guard(request):
        return r
    q = request.query_params
    items = CONTACTS
    for key in ("email", "phone", "mobile"):
        if q.get(key):
            items = [c for c in items if c.get(key) == q[key]]
    if q.get("company_id"):
        items = [c for c in items if c["company_id"] == int(q["company_id"])]
    return paged(request, items)


async def get_contact(request: Request):
    if r := _guard(request):
        return r
    cid = int(request.path_params["cid"])
    c = next((c for c in CONTACTS if c["id"] == cid), None)
    return JSONResponse(c, headers=_rl_headers()) if c else _err(404, "Not found")


app = Starlette(
    routes=[
        Route("/api/v2/agents/me", me),
        Route("/api/v2/tickets", list_tickets),
        Route("/api/v2/tickets/{tid:int}", get_ticket),
        Route("/api/v2/tickets/{tid:int}/conversations", conversations),
        Route("/api/v2/search/tickets", search_tickets),
        Route("/api/v2/contacts", list_contacts),
        Route("/api/v2/contacts/{cid:int}", get_contact),
    ]
)

if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=8765)
