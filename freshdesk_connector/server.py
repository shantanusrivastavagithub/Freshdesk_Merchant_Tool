"""MCP server exposing read-only Freshdesk tools to a Razorpay Agent Studio agent.

Run locally (stdio):
    python -m freshdesk_connector.server

Run as remote server (HTTP):
    python -m freshdesk_connector.server --http --port 8000
    -> MCP endpoint at http://<host>:8000/mcp, protected by CONNECTOR_BEARER_TOKEN.
"""

from __future__ import annotations

import argparse
import hmac
import logging
import os
from typing import Annotated, Any, Awaitable, Literal, Optional

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from .client import FreshdeskClient
from .config import Settings, load_dotenv
from .errors import FreshdeskError
from .service import FreshdeskService

log = logging.getLogger("freshdesk_connector")

INSTRUCTIONS = """
Read-only access to a Freshdesk helpdesk (tickets, conversations, contacts).
You CANNOT create, update, reply to, assign or close tickets.
Use search_freshdesk_tickets for status/priority/tag/date filters, list_freshdesk_tickets for a requester's tickets or recent activity, and get_freshdesk_ticket + list_freshdesk_ticket_conversations to read a specific ticket thread.
Private (internal) notes are hidden unless explicitly requested; never quote them to end customers.
If a tool returns an 'error' field, explain it to the user instead of retrying in a loop.
"""

mcp = FastMCP("freshdesk-connector", instructions=INSTRUCTIONS)

_service: FreshdeskService | None = None


def _svc() -> FreshdeskService:
    global _service
    if _service is None:
        _service = FreshdeskService(FreshdeskClient(Settings.from_env()))
    return _service


async def _run(coro: Awaitable[dict[str, Any]]) -> dict[str, Any]:
    try:
        return await coro
    except FreshdeskError as exc:
        log.info("Tool error: %s", exc.message)
        return exc.to_dict()


Status = Optional[Literal["open", "pending", "resolved", "closed"]]
Priority = Optional[Literal["low", "medium", "high", "urgent"]]


@mcp.tool()
async def check_freshdesk_connection() -> dict[str, Any]:
    """Verify the Freshdesk API key works and show which agent account and helpdesk it is connected to."""
    return await _run(_svc().check_connection())


@mcp.tool()
async def list_freshdesk_tickets(
    requester_email: Annotated[Optional[str], Field(description="Only tickets raised by this customer email")] = None,
    requester_id: Annotated[Optional[int], Field(description="Only tickets from this contact id")] = None,
    company_id: Annotated[Optional[int], Field(description="Only tickets from this company id")] = None,
    updated_since: Annotated[Optional[str], Field(description="YYYY-MM-DD; tickets updated on/after this date. Without it only the last 30 days are returned")] = None,
    predefined_filter: Annotated[Optional[Literal["new_and_my_open", "watching", "spam", "deleted"]], Field(description="Freshdesk built-in view")] = None,
    order_by: Literal["created_at", "updated_at", "due_by", "status"] = "updated_at",
    order_type: Literal["asc", "desc"] = "desc",
    page: Annotated[int, Field(ge=1)] = 1,
    per_page: Annotated[int, Field(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    """List tickets (newest activity first by default), optionally filtered by requester, company or update date. Paginate with page while has_more is true."""
    return await _run(
        _svc().list_tickets(
            requester_email=requester_email,
            requester_id=requester_id,
            company_id=company_id,
            updated_since=updated_since,
            predefined_filter=predefined_filter,
            order_by=order_by,
            order_type=order_type,
            page=page,
            per_page=per_page,
        )
    )


@mcp.tool()
async def search_freshdesk_tickets(
    status: Annotated[Status, Field(description="Ticket status")] = None,
    priority: Annotated[Priority, Field(description="Ticket priority")] = None,
    tag: Annotated[Optional[str], Field(description="Exact tag, e.g. 'refund'")] = None,
    ticket_type: Annotated[Optional[str], Field(description="Ticket type, e.g. 'Question', 'Incident'")] = None,
    agent_id: Annotated[Optional[int], Field(description="Assigned agent id")] = None,
    group_id: Annotated[Optional[int], Field(description="Assigned group id")] = None,
    created_from: Annotated[Optional[str], Field(description="YYYY-MM-DD, created on/after")] = None,
    created_to: Annotated[Optional[str], Field(description="YYYY-MM-DD, created on/before")] = None,
    updated_from: Annotated[Optional[str], Field(description="YYYY-MM-DD, updated on/after")] = None,
    page: Annotated[int, Field(ge=1, le=10, description="30 results per page, max 10 pages")] = 1,
) -> dict[str, Any]:
    """Search tickets by status, priority, tag, type, assignee or date range (filters are ANDed). At least one filter is required. Results may lag real-time by a few minutes."""
    return await _run(
        _svc().search_tickets(
            status=status,
            priority=priority,
            tag=tag,
            ticket_type=ticket_type,
            agent_id=agent_id,
            group_id=group_id,
            created_from=created_from,
            created_to=created_to,
            updated_from=updated_from,
            page=page,
        )
    )


@mcp.tool()
async def get_freshdesk_ticket(
    ticket_id: Annotated[int, Field(ge=1, description="Freshdesk ticket number")],
) -> dict[str, Any]:
    """Get full details of one ticket: subject, description, status, priority, requester, custom fields, SLA dates and stats."""
    return await _run(_svc().get_ticket(ticket_id))


@mcp.tool()
async def list_freshdesk_ticket_conversations(
    ticket_id: Annotated[int, Field(ge=1, description="Freshdesk ticket number")],
    include_private_notes: Annotated[bool, Field(description="Include internal agent notes. Only set true for internal/agent-facing use")] = False,
    page: Annotated[int, Field(ge=1)] = 1,
    per_page: Annotated[int, Field(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    """Read the reply thread of a ticket (customer replies and agent replies, oldest first)."""
    return await _run(
        _svc().list_ticket_conversations(
            ticket_id,
            include_private_notes=include_private_notes,
            page=page,
            per_page=per_page,
        )
    )


@mcp.tool()
async def get_freshdesk_contact(
    contact_id: Annotated[int, Field(ge=1, description="Contact (requester) id")],
) -> dict[str, Any]:
    """A customer profile looked up by id."""
    return await _run(_svc().get_contact(contact_id))


@mcp.tool()
async def find_freshdesk_contacts(
    email: Optional[str] = None,
    phone: Optional[str] = None,
    mobile: Optional[str] = None,
    company_id: Optional[int] = None,
    page: Annotated[int, Field(ge=1)] = 1,
    per_page: Annotated[int, Field(ge=1, le=100)] = 30,
) -> dict[str, Any]:
    """Find contacts by exact email, phone, mobile or company id."""
    return await _run(
        _svc().find_contacts(
            email=email,
            phone=phone,
            mobile=mobile,
            company_id=company_id,
            page=page,
            per_page=per_page,
        )
    )


# HTTP mode middleware
class BearerAuthMiddleware:
    """Pure-ASGI middleware: require 'Authorization: Bearer <token>' on HTTP requests."""

    def __init__(self, app, token: str) -> None:
        self.app = app
        self.expected = f"Bearer {token}".encode()

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] == "http":
            headers = dict(scope.get("headers") or [])
            auth = headers.get(b"authorization", b"")
            if not hmac.compare_digest(auth, self.expected):
                await send(
                    {
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"www-authenticate", b"Bearer"),
                        ],
                    }
                )
                await send(
                    {
                        "type": "http.response.body",
                        "body": b'{"error": "unauthorized"}',
                    }
                )
                return
        await self.app(scope, receive, send)


def main() -> None:
    load_dotenv()
    parser = argparse.ArgumentParser(description="Freshdesk MCP connector for Agent Studio")
    parser.add_argument("--http", action="store_true", help="Serve Streamable HTTP instead of stdio")
    parser.add_argument("--host", default=os.environ.get("HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", 8000)))
    args = parser.parse_args()

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    Settings.from_env()  # fail fast on missing config

    if not args.http:
        mcp.run(transport="stdio")
        return

    import uvicorn

    token = os.environ.get("CONNECTOR_BEARER_TOKEN")
    app = mcp.streamable_http_app()
    if token:
        app = BearerAuthMiddleware(app, token)
    else:
        log.warning("CONNECTOR_BEARER_TOKEN not set. HTTP endpoint is UNAUTHENTICATED. Do not expose publicly.")

    log.info("MCP endpoint: http://%s:%d/mcp", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
