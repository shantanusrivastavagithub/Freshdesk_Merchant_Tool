"""Async rate limiting.

Two layers:
1. A token bucket that proactively keeps us under requests/minute.
2. A shared "cooldown" that any request can set when Freshdesk returns 429 with Retry-After, so *all* concurrent callers pause, not just the one that got throttled.
"""

from __future__ import annotations

import asyncio
import time
from typing import Awaitable, Callable

Sleep = Callable[[float], Awaitable[None]]


class RateLimiter:
    def __init__(
        self,
        requests_per_minute: int,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Sleep = asyncio.sleep,
    ) -> None:
        if requests_per_minute < 0:
            raise ValueError("requests_per_minute must be >= 0")
        self.capacity = float(requests_per_minute)
        self._refill_per_sec = requests_per_minute / 60.0
        self._tokens = self.capacity
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._cooldown_until = 0.0
        self._lock = asyncio.Lock()

        # Last values reported by Freshdesk headers (for observability).
        self.server_remaining: int | None = None
        self.server_total: int | None = None

    def _refill(self) -> None:
        now = self._clock()
        self._tokens = min(
            self.capacity,
            self._tokens + (now - self._last) * self._refill_per_sec,
        )
        self._last = now

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = self._clock()
                if now < self._cooldown_until:
                    await self._sleep(self._cooldown_until - now)
                    continue
                self._refill()
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await self._sleep((1 - self._tokens) / self._refill_per_sec)

    def cooldown(self, seconds: float) -> None:
        """Block all callers for 'seconds' (called on HTTP 429)."""
        until = self._clock() + max(0.0, seconds)
        self._cooldown_until = max(self._cooldown_until, until)
        self._tokens = 0.0

    def observe_headers(self, headers) -> None:
        """Track Freshdesk's X-RateLimit-* headers; drain the bucket if the server says we're almost out (e.g. other integrations share the quota)."""
        try:
            remaining = headers.get("x-ratelimit-remaining")
            total = headers.get("x-ratelimit-total")
            if total is not None:
                self.server_total = int(total)
            if remaining is not None:
                self.server_remaining = int(remaining)
            if self.server_remaining < 1:
                self._tokens = 0.0
        except (TypeError, ValueError):
            pass
