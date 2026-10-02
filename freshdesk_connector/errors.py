"""Typed connector errors. Messages are written to be shown to an LLM agent."""

from __future__ import annotations


class FreshdeskError(Exception):
    def __init__(self, message: str, status: int | None = None, details: object = None):
        super().__init__(message)
        self.message = message
        self.status = status
        self.details = details

    def to_dict(self) -> dict:
        out: dict = {"error": type(self).__name__, "message": self.message}
        if self.status is not None:
            out["status"] = self.status
        if self.details:
            out["details"] = self.details
        return out


class AuthenticationError(FreshdeskError):
    """401 — API key missing/invalid."""


class PermissionDeniedError(FreshdeskError):
    """403 — key valid but the agent's role lacks access (or API disabled on plan)."""


class NotFoundError(FreshdeskError):
    """404 — the requested ticket/contact does not exist or is not visible."""


class RateLimitError(FreshdeskError):
    """429 that persisted after all retries."""

    def __init__(self, message: str, retry_after: float | None = None, **kw):
        super().__init__(message, status=429, **kw)
        self.retry_after = retry_after


class ValidationError(FreshdeskError):
    """400 from Freshdesk or bad input caught locally before calling the API."""
