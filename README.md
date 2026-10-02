# Freshdesk Merchant Tool

A read-only Freshdesk MCP connector for Razorpay Agent Studio. It gives an agent safe, read-only access to ticket, contact, and conversation data without allowing writes or destructive actions.

## What this project does

The connector exposes a small, controlled API surface for Freshdesk:

- Check Freshdesk connectivity
- List tickets with filters like requester, company, and updated date
- Search tickets by status, priority, tag, type, agent, group, and date windows
- Retrieve a single ticket with details and custom fields
- Read ticket conversations while hiding internal/private notes by default
- Look up contacts by id, email, phone, mobile, or company
- Expose the functionality through MCP tools for agent orchestration

This project is intentionally read-only. It does not create, update, delete, assign, merge, close, or reply to Freshdesk records.

## Project structure

- `freshdesk_connector/` — connector logic, validation, error handling, rate limiting, and MCP server
- `mock_server/` — local mock Freshdesk API for local testing and demos
- `docs/AGENT_CAPABILITIES.md` — capability and guardrail documentation
- `scripts/` — demo client for MCP HTTP calls
- `tests/` — offline tests using `httpx.MockTransport`
- `.env.example` — sample environment variables

## Requirements

- Python 3.10+
- `pip`

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then update `.env` with your Freshdesk details:

```env
FRESHDESK_DOMAIN=acme
FRESHDESK_API_KEY=your-api-key
FRESHDESK_MAX_RPM=40
FRESHDESK_MAX_RETRIES=4
FRESHDESK_TIMEOUT=20
CONNECTOR_BEARER_TOKEN=some-random-token
PORT=8000
LOG_LEVEL=INFO
```

For the local mock server, you can also use:

```env
FRESHDESK_DOMAIN=http://127.0.0.1:8765
FRESHDESK_API_KEY=mock-api-key
```

## Run the mock Freshdesk server

```bash
python mock_server/mock_freshdesk.py
```

This starts a local mock API on `http://127.0.0.1:8765`.

## Run the MCP server

Stdio mode:

```bash
python -m freshdesk_connector.server
```

HTTP mode:

```bash
python -m freshdesk_connector.server --http --port 8000
```

When running HTTP mode, the client must send:

```http
Authorization: Bearer <CONNECTOR_BEARER_TOKEN>
```

## CLI usage

```bash
python -m freshdesk_connector.cli check
python -m freshdesk_connector.cli list --email jane@example.com --per-page 5
python -m freshdesk_connector.cli search --status open --priority high --page 1
python -m freshdesk_connector.cli get 42
python -m freshdesk_connector.cli convos 42
python -m freshdesk_connector.cli contact --email jane@example.com
```

## MCP demo client

```bash
python scripts/mcp_client_demo.py
```

This connects to the local HTTP MCP endpoint and lists available tools before calling `search_freshdesk_tickets`.

## Validation

Run the test suite:

```bash
python -m pytest -q
```

The project includes offline tests with `httpx.MockTransport`, so validation does not require a live Freshdesk account.

## Notes

- Private notes are hidden by default.
- Search queries are validated and constructed from structured inputs instead of raw user-supplied query strings.
- HTTP retry logic handles 429 `Retry-After`, 5xx status codes, and connection timeouts.
- The connector is designed to be safe and bounded for LLM-driven agent use.
