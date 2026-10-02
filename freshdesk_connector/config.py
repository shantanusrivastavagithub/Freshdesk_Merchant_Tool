"""Configuration for the Freshdesk connector.

All settings come from environment variables (optionally loaded from a local .env file).
Nothing secret is ever hard-coded or logged.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: str | os.PathLike = ".env") -> None:
    """Minimal .env loader (KEY=VALUE lines). Existing env vars win."""
    p = Path(path)
    if not p.is_file():
        return
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def normalize_domain(domain: str) -> str:
    """Accept 'acme', 'acme.freshdesk.com', or 'https://acme.freshdesk.com/'
    and return a full base URL like 'https://acme.freshdesk.com'.

    A full http(s) URL with a non-freshdesk host is kept as-is (used for the local mock server, e.g. http://127.0.0.1:8765).
    """
    d = domain.strip().rstrip("/")
    if not d:
        raise ValueError("FRESHDESK_DOMAIN is empty")
    if d.startswith(("http://", "https://")):
        return d
    if "." not in d:
        d = f"{d}.freshdesk.com"
    if not re.fullmatch(r"[A-Za-z0-9.-]+", d):
        raise ValueError(f"Invalid FRESHDESK_DOMAIN: {domain!r}")
    return f"https://{d}"


@dataclass(frozen=True)
class Settings:
    base_url: str
    api_key: str

    # Client-side throttle. Freshdesk plan limits are per-minute per-account
    # (e.g. ~50/min on Growth, more on higher plans). Keep below your plan.
    max_requests_per_minute: int = 40
    max_retries: int = 4
    timeout_seconds: float = 20.0
    max_retry_wait_seconds: float = 60.0

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv()
        domain = os.environ.get("FRESHDESK_DOMAIN", "")
        api_key = os.environ.get("FRESHDESK_API_KEY", "")
        if not domain or not api_key:
            raise RuntimeError(
                "FRESHDESK_DOMAIN and FRESHDESK_API_KEY must be set "
                "(see .env.example)."
            )
        return cls(
            base_url=normalize_domain(domain),
            api_key=api_key,
            max_requests_per_minute=int(os.environ.get("FRESHDESK_MAX_RPM", 40)),
            max_retries=int(os.environ.get("FRESHDESK_MAX_RETRIES", 4)),
            timeout_seconds=float(os.environ.get("FRESHDESK_TIMEOUT", 20)),
        )
