"""Low-level, read-only HTTP client for the Freshdesk v2 REST API.

- Auth: HTTP Basic with the agent's API key as username and "X" as password (this is Freshdesk's documented API-key scheme).
- Only GET is exposed — the connector is read-only by construction.
- Handles 429 (Retry-After), 5xx and network errors with bounded retries.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
from typing import Any, Awaitable, Callable

import httpx

from .config import Settings
from .errors import (
    AuthenticationError,
    FreshdeskError,
    NotFoundError,
    PermissionDeniedError,
    RateLimitError,
    ValidationError,
)
from .rate_limiter import RateLimiter

log = logging.getLogger("freshdesk_connector")

RETRYABLE_STATUS = {500, 502, 503, 504}
_NEXT_LINK_RE = re.compile(r'<([^>]+)>;\s*rel="next"')


class FreshdeskClient:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.settings = settings
        self._sleep = sleep
        self.limiter = RateLimiter(settings.max_requests_per_minute, sleep=sleep)
        self._http = httpx.AsyncClient(
            base_url=f"{settings.base_url}/api/v2",
            auth=httpx.BasicAuth(settings.api_key, "X"),
            timeout=settings.timeout_seconds,
            headers={
                "Accept": "application/json",
                "User-Agent": "agent-studio-freshdesk-connector/1.0",
            },
            transport=transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> FreshdeskClient:
        return self

    async def __aexit__(self, *exc) -> None:
        await self.aclose()

    # Core
    async def get(self, path: str, params: dict[str, Any] | None = None) -> tuple[Any, bool]:
        """GET path and return (json_body, has_next_page)."""
        params = {k: v for k, v in (params or {}).items() if v is not None}
        attempt = 0
        while True:
            await self.limiter.acquire()
            try:
                resp = await self._http.get(path, params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.settings.max_retries:
                    raise FreshdeskError(f"Network error talking to Freshdesk: {exc}") from exc
                await self._backoff(attempt, reason=type(exc).__name__)
                attempt += 1
                continue

            self.limiter.observe_headers(resp.headers)

            if resp.status_code == 429:
                retry_after = _parse_retry_after(resp.headers.get("retry-after"))
                if attempt >= self.settings.max_retries or retry_after > self.settings.max_retry_wait_seconds:
                    raise RateLimitError(
                        f"Freshdesk rate limit exceeded; retry after {int(retry_after)}s.",
                        retry_after=retry_after,
                    )
                log.warning("429 from Freshdesk, cooling down %.1fs", retry_after)
                self.limiter.cooldown(retry_after)
                attempt += 1
                continue

            if resp.status_code in RETRYABLE_STATUS:
                if attempt >= self.settings.max_retries:
                    raise _to_error(resp)
                await self._backoff(attempt, reason=f"HTTP {resp.status_code}")
                attempt += 1
                continue

            if resp.is_success:
                has_next = bool(_NEXT_LINK_RE.search(resp.headers.get("link", "")))
                return resp.json(), has_next

            raise _to_error(resp)

    async def _backoff(self, attempt: int, reason: str) -> None:
        delay = min(
            self.settings.max_retry_wait_seconds,
            (2 ** attempt) + random.uniform(0, 0.5),
        )
        log.warning("Retrying Freshdesk call after %s in %.1fs", reason, delay)
        await self._sleep(delay)


def _parse_retry_after(value: str | None) -> float:
    try:
        return max(1.0, float(value)) if value is not None else 30.0
    except ValueError:
        return 30.0


def _to_error(resp: httpx.Response) -> FreshdeskError:
    try:
        body = resp.json()
    except ValueError:
        body = {"raw": resp.text[:500]}
    details = body.get("errors") if isinstance(body, dict) else None
    desc = body.get("description") if isinstance(body, dict) else None
    status = resp.status_code
    if status == 401:
        return AuthenticationError(
            "Freshdesk rejected the API key (401). Check FRESHDESK_API_KEY.", status
        )
    if status == 403:
        return PermissionDeniedError(
            "Access denied (403). The agent's role may lack permission or API access is disabled on this plan.",
            status,
        )
    if status == 404:
        return NotFoundError("Resource not found (404).", status)
    if status == 400:
        return ValidationError(
            desc or "Freshdesk rejected the request (400).", status, details
        )
    return FreshdeskError(desc or f"Freshdesk returned HTTP {status}.", status, details)
