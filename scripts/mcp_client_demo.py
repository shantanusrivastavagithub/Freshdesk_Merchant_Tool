"""Minimal MCP client to verify the HTTP server end-to-end (what Agent Studio does).

Usage:
    python scripts/mcp_client_demo.py [url] [bearer_token]
"""

from __future__ import annotations

import asyncio
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000/mcp"
TOKEN = sys.argv[2] if len(sys.argv) > 2 else os.environ.get("CONNECTOR_BEARER_TOKEN", "")


async def main() -> None:
    async with streamablehttp_client(URL, headers={"Authorization": f"Bearer {TOKEN}"}) as (r, w, _):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            print("tools:", [t.name for t in tools.tools])
            res = await s.call_tool("search_freshdesk_tickets", {"status": "open", "tag": "refund"})
            print(res.content[0].text[:600])


if __name__ == "__main__":
    asyncio.run(main())
