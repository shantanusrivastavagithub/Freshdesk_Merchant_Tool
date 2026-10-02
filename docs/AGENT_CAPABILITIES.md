# What the Freshdesk agent can and cannot do

This connector gives a Razorpay Agent Studio agent **read-only** access to one Freshdesk helpdesk, through 7 MCP tools.

### What it can do

| Tool | Purpose | Example question it answers |
| --- | --- | --- |
| `check_freshdesk_connection` | Confirms the API key works and shows which agent account it uses | "Are you connected to Freshdesk?" |
| `list_freshdesk_tickets` | Lists tickets with optional requester / company / `updated_since` filters. Sorted and paginated | "Show tickets from jane@example.com" |
| `search_freshdesk_tickets` | Searches by status, priority, tag, type, agent, group or date range (all filters combined with AND) | "How many urgent open refund tickets were created this week?" |
| `get_freshdesk_ticket` | Full details for one ticket: description, requester, custom fields, SLA dates, stats | "What is ticket #4521 about?" |
| `get_freshdesk_contact_conversations` | The reply thread. Private notes are hidden by default | "What did we last tell the customer on #4521?" |
| `get_freshdesk_contact` | A customer profile looked up by id | "Who raised ticket #4521?" |
| `find_freshdesk_contacts` | Finds contacts by exact email, phone, mobile or company id | "Do we have a contact for +91 9800000012?" |

**Other behaviour:**
- Status, priority and source codes come back as labels ("Open", "Urgent", "Email").
- Long descriptions and replies are cut off at 2,000 characters to keep context small.
- Paginated results include `has_more`, so the agent can ask for the next page.
- When something fails, the agent gets a structured error it can explain to the user: `AuthenticationError`, `PermissionDeniedError`, `NotFoundError`, `RateLimitError` or `ValidationError`.

### What it cannot do

- **No writes:** It can't create, reply to, update, assign, merge, close or delete tickets, and it can't change contacts. The MCP client only has a GET method.
- **No free-text or full-text search:** Freshdesk's search API only filters on fields, so the agent can't search for "tickets mentioning UPI". It also can't search by requester email, but `list_freshdesk_tickets(requester_email=...)` covers that.
- **No raw Freshdesk query language:** The connector builds queries from validated, structured inputs so they can't be injected.
- **No attachments:** It returns attachment file names only, not the files.
- **No other Freshdesk modules:** Solutions/KB articles, companies, agents, groups, time entries, satisfaction ratings and Freshdesk Messaging/Freshchat aren't exposed.
- **No real-time events:** There are no webhooks. Search results can be a few minutes behind.
- **Bounded result sets:** Search returns at most 300 results (10 pages of 30). A list call without `updated_since` only covers tickets created in the last 30 days.
- **No per-end-user permissions:** Every call runs as the one Freshdesk agent whose API key is configured, so the agent sees whatever that Freshdesk role can see.

## Guidance for the agent (also sent to it as MCP server instructions)

- Use search for status/priority/tag/date questions. Use list for "tickets of customer X".
- Never quote private notes to end customers.
- On `RateLimitError`, tell the user to retry after `retry_after` seconds rather than looping.
