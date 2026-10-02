"""Agent-facing operations (list / get / search) built on FreshdeskClient.

Responsibilities:
- Validate & translate friendly inputs ("open", "high") to Freshdesk codes.
- Build safe Freshdesk search queries (no raw query injection).
- Trim responses to compact, LLM-friendly shapes with human-readable labels.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from .client import FreshdeskClient
from .errors import ValidationError

STATUS = {2: "Open", 3: "Pending", 4: "Resolved", 5: "Closed"}
PRIORITY = {1: "Low", 2: "Medium", 3: "High", 4: "Urgent"}
SOURCE = {
    1: "Email",
    2: "Portal",
    3: "Phone",
    7: "Chat",
    9: "Feedback Widget",
    10: "Outbound Email",
}

MAX_TEXT = 2000  # chars of description/body returned per item
SEARCH_PAGE_SIZE = 30  # fixed by Freshdesk
SEARCH_MAX_PAGE = 10  # Freshdesk caps search at 10 pages (300 results)
MAX_QUERY_LEN = 512  # Freshdesk search query length limit

_SAFE_TAG = re.compile(r"^[\w .-]+$")


# --- Helpers ---


def _code(value: int | str | None, table: dict[int, str], field: str) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        v = int(value)
        if v in table:
            return v
    else:
        rev = {name.lower(): k for k, name in table.items()}
        if value.strip().lower() in rev:
            return rev[value.strip().lower()]
    allowed = ", ".join(f"{k}={n}" for k, n in table.items())
    raise ValidationError(f"Invalid {field} {value!r}. Allowed: {allowed}")


def _date(value: str | None, field: str) -> str | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10]).isoformat()
    except ValueError as exc:
        raise ValidationError(f"{field} must be YYYY-MM-DD, got {value!r}") from exc


def _page(page: int, per_page: int, max_per_page: int = 100) -> tuple[int, int]:
    if page < 1:
        raise ValidationError("page must be >= 1")
    if not 1 <= per_page <= max_per_page:
        raise ValidationError(f"per_page must be between 1 and {max_per_page}")
    return page, per_page


def _clip(text: str | None, limit: int = MAX_TEXT) -> str | None:
    if text is None:
        return None
    text = text.strip()
    return text if len(text) <= limit else text[:limit] + " [truncated]"


def ticket_summary(t: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": t.get("id"),
        "subject": t.get("subject"),
        "status": STATUS.get(t.get("status"), t.get("status")),
        "priority": PRIORITY.get(t.get("priority"), t.get("priority")),
        "source": SOURCE.get(t.get("source"), t.get("source")),
        "type": t.get("type"),
        "requester_id": t.get("requester_id"),
        "responder_id": t.get("responder_id"),
        "group_id": t.get("group_id"),
        "company_id": t.get("company_id"),
        "tags": t.get("tags") or [],
        "is_escalated": t.get("is_escalated"),
        "due_by": t.get("due_by"),
        "created_at": t.get("created_at"),
        "updated_at": t.get("updated_at"),
    }


def ticket_detail(t: dict[str, Any]) -> dict[str, Any]:
    out = ticket_summary(t)
    reg = t.get("requester") or {}
    out.update(
        {
            "description": _clip(t.get("description_text")),
            "requester": {k: reg.get(k) for k in ("id", "name", "email", "phone")} if reg else None,
            "cc_emails": t.get("cc_emails") or [],
            "fr_due_by": t.get("fr_due_by"),
            "custom_fields": t.get("custom_fields") or {},
            "stats": t.get("stats"),
        }
    )
    return out


def conversation_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": c.get("id"),
        "kind": "private_note" if c.get("private") else ("customer_reply" if c.get("incoming") else "agent_reply"),
        "user_id": c.get("user_id"),
        "from_email": c.get("from_email"),
        "body": _clip(c.get("body_text")),
        "attachments": [a.get("name") for a in c.get("attachments") or []],
        "created_at": c.get("created_at"),
    }


def contact_summary(c: dict[str, Any]) -> dict[str, Any]:
    return {k: c.get(k) for k in (
        "id", "name", "email", "phone",
        "mobile", "company_id", "job_title",
        "language", "time_zone", "active", "created_at", "updated_at"
    )}


def build_ticket_query(
    status: int | str | None = None,
    priority: int | str | None = None,
    tag: str | None = None,
    ticket_type: str | None = None,
    agent_id: int | None = None,
    group_id: int | None = None,
    created_from: str | None = None,
    created_to: str | None = None,
    updated_from: str | None = None,
) -> str:
    """Build a Freshdesk ticket search query from structured filters.

    Values are validated/whitelisted so the agent can't inject arbitrary query syntax.
    """
    parts: list[str] = []
    if (s := _code(status, STATUS, "status")) is not None:
        parts.append(f"status:{s}")
    if (p := _code(priority, PRIORITY, "priority")) is not None:
        parts.append(f"priority:{p}")
    for name, val in (("tag", tag), ("type", ticket_type)):
        if val:
            if not _SAFE_TAG.match(val):
                raise ValidationError(f"{name} contains unsupported characters: {val!r}")
            parts.append(f"{name}:'{val}'")
    for name, val in (("agent_id", agent_id), ("group_id", group_id)):
        if val is not None:
            parts.append(f"{name}:{int(val)}")
    if d := _date(created_from, "created_from"):
        parts.append(f"created_at:>'{d}'")
    if d := _date(created_to, "created_to"):
        parts.append(f"created_at:<'{d}'")
    if d := _date(updated_from, "updated_from"):
        parts.append(f"updated_at:>'{d}'")
    if not parts:
        raise ValidationError("Provide at least one search filter (status, priority, tag, type, agent_id, group_id or a date).")
    query = " AND ".join(parts)
    if len(query) > MAX_QUERY_LEN:
        raise ValidationError("Search query too long; use fewer filters.")
    return query


class FreshdeskService:
    def __init__(self, client: FreshdeskClient) -> None:
        self.client = client

    async def check_connection(self) -> dict[str, Any]:
        me, _ = await self.client.get("/agents/me")
        contact = me.get("contact") or {}
        return {
            "connected": True,
            "helpdesk": self.client.settings.base_url,
            "authenticated_as": {
                "agent_id": me.get("id"),
                "name": contact.get("name"),
                "email": contact.get("email"),
            },
            "rate_limit": {
                "remaining_this_minute": self.client.limiter.server_remaining,
                "total_per_minute": self.client.limiter.server_total,
            },
        }

    async def list_tickets(
        self,
        *,
        requester_email: str | None = None,
        requester_id: int | None = None,
        company_id: int | None = None,
        updated_since: str | None = None,
        predefined_filter: str | None = None,
        order_by: str = "updated_at",
        order_type: str = "desc",
        page: int = 1,
        per_page: int = 30,
    ) -> dict[str, Any]:
        page, per_page = _page(page, per_page)
        if order_by not in ("created_at", "updated_at", "due_by", "status"):
            raise ValidationError("order_by must be one of created_at, updated_at, due_by, status")
        if order_type not in ("asc", "desc"):
            raise ValidationError("order_type must be asc or desc")
        if predefined_filter and predefined_filter not in ("new_and_my_open", "watching", "spam", "deleted"):
            raise ValidationError("predefined_filter must be new_and_my_open, watching, spam or deleted")
        since = _date(updated_since, "updated_since")
        params = {
            "email": requester_email,
            "requester_id": requester_id,
            "company_id": company_id,
            "updated_since": f"{since}T00:00:00Z" if since else None,
            "filter": predefined_filter,
            "order_by": order_by,
            "order_type": order_type,
            "page": page,
            "per_page": per_page,
        }
        data, has_next = await self.client.get("/tickets", params)
        return {
            "tickets": [ticket_summary(t) for t in data],
            "page": page,
            "per_page": per_page,
            "has_more": has_next,
            "note": None if since else "Without updated_since Freshdesk only returns tickets created in the last 30 days.",
        }

    async def get_ticket(self, ticket_id: int) -> dict[str, Any]:
        data, _ = await self.client.get(f"/tickets/{int(ticket_id)}", {"include": "requester,stats"})
        return ticket_detail(data)

    async def list_ticket_conversations(
        self,
        ticket_id: int,
        *,
        include_private_notes: bool = False,
        page: int = 1,
        per_page: int = 30,
    ) -> dict[str, Any]:
        page, per_page = _page(page, per_page)
        data, has_next = await self.client.get(
            f"/tickets/{int(ticket_id)}/conversations",
            {"page": page, "per_page": per_page},
        )
        convs = [conversation_summary(c) for c in data]
        hidden = 0
        if not include_private_notes:
            hidden = sum(1 for c in convs if c["kind"] == "private_note")
            convs = [c for c in convs if c["kind"] != "private_note"]
        return {
            "ticket_id": int(ticket_id),
            "conversations": convs,
            "private_notes_hidden": hidden,
            "page": page,
            "has_more": has_next,
        }

    async def search_tickets(self, *, page: int = 1, **filters: Any) -> dict[str, Any]:
        if not 1 <= page <= SEARCH_MAX_PAGE:
            raise ValidationError(f"Search page must be between 1 and {SEARCH_MAX_PAGE}")
        query = build_ticket_query(**filters)
        data, _ = await self.client.get("/search/tickets", {"query": f'"{query}"', "page": page})
        total = data.get("total", 0)
        return {
            "query": query,
            "total": total,
            "page": page,
            "tickets": [ticket_summary(t) for t in data.get("results", [])],
            "has_more": page * SEARCH_PAGE_SIZE < min(total, SEARCH_PAGE_SIZE * SEARCH_MAX_PAGE),
        }

    async def get_contact(self, contact_id: int) -> dict[str, Any]:
        data, _ = await self.client.get(f"/contacts/{int(contact_id)}")
        return contact_summary(data)

    async def find_contacts(
        self,
        *,
        email: str | None = None,
        phone: str | None = None,
        mobile: str | None = None,
        company_id: int | None = None,
        page: int = 1,
        per_page: int = 30,
    ) -> dict[str, Any]:
        if not any((email, phone, mobile, company_id)):
            raise ValidationError("Provide at least one of email, phone, mobile, company_id.")
        page, per_page = _page(page, per_page)
        data, has_next = await self.client.get(
            "/contacts",
            {
                "email": email,
                "phone": phone,
                "mobile": mobile,
                "company_id": company_id,
                "page": page,
                "per_page": per_page,
            },
        )
        return {
            "contacts": [contact_summary(c) for c in data],
            "page": page,
            "has_more": has_next,
        }
