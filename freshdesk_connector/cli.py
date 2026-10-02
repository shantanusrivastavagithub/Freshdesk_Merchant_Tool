"""Tiny CLI to smoke-test the connector without an agent.

Examples:
    python -m freshdesk_connector.cli check
    python -m freshdesk_connector.cli list --per-page 5
    python -m freshdesk_connector.cli search --status open --priority high
    python -m freshdesk_connector.cli get 42
    python -m freshdesk_connector.cli convos 42
    python -m freshdesk_connector.cli contact --email jane@example.com
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from .client import FreshdeskClient
from .config import Settings
from .errors import FreshdeskError
from .service import FreshdeskService


async def _main(args: argparse.Namespace) -> int:
    async with FreshdeskClient(Settings.from_env()) as client:
        svc = FreshdeskService(client)
        try:
            if args.cmd == "check":
                out = await svc.check_connection()
            elif args.cmd == "list":
                out = await svc.list_tickets(
                    requester_email=args.email,
                    updated_since=args.updated_since,
                    page=args.page,
                    per_page=args.per_page,
                )
            elif args.cmd == "search":
                out = await svc.search_tickets(
                    status=args.status,
                    priority=args.priority,
                    tag=args.tag,
                    created_from=args.created_from,
                    page=args.page,
                )
            elif args.cmd == "get":
                out = await svc.get_ticket(args.ticket_id)
            elif args.cmd == "convos":
                out = await svc.list_ticket_conversations(
                    args.ticket_id, include_private_notes=args.private
                )
            elif args.cmd == "contact":
                out = await svc.find_contacts(email=args.email, phone=args.phone)
            else:
                return 2
        except FreshdeskError as exc:
            print(json.dumps(exc.to_dict(), indent=2), file=sys.stderr)
            return 1

        print(json.dumps(out, indent=2, default=str))
        return 0


def main() -> None:
    p = argparse.ArgumentParser(prog="freshdesk-cli")
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("check")

    l = sub.add_parser("list")
    l.add_argument("--email")
    l.add_argument("--updated-since")
    l.add_argument("--page", type=int, default=1)
    l.add_argument("--per-page", type=int, default=10)

    s = sub.add_parser("search")
    s.add_argument("--status")
    s.add_argument("--priority")
    s.add_argument("--tag")
    s.add_argument("--created-from")
    s.add_argument("--page", type=int, default=1)

    g = sub.add_parser("get")
    g.add_argument("ticket_id", type=int)

    c = sub.add_parser("convos")
    c.add_argument("ticket_id", type=int)
    c.add_argument("--private", action="store_true")

    ct = sub.add_parser("contact")
    ct.add_argument("--email")
    ct.add_argument("--phone")

    sys.exit(asyncio.run(_main(p.parse_args())))


if __name__ == "__main__":
    main()
