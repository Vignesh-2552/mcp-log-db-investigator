# Investigation MCP Server

A [FastMCP](https://gofastmcp.com) server exposing safe, **read-only** tools for
investigating support tickets across three sources: schema discovery + query
execution against **PostgreSQL**, log/metric access against **AWS CloudWatch**,
and log/event access against **New Relic** (NRQL). It's driven by a client
model (Claude Desktop, Cursor) that writes the queries in conversation — the
server's job is to supply enough grounding context (schema, field discovery,
a query cookbook) that the model generates correct queries, and to validate +
execute them safely. See [`docs/design.md`](docs/design.md) for the full design.

## Architecture

![Architecture: MCP client to Investigation MCP Server, fanning out to PostgreSQL, AWS CloudWatch, and New Relic](docs/architecture.svg)

## Setup

```powershell
uv sync
copy .env.example .env
# Edit .env with your DB_URL and other settings
uv run pytest tests/unit      # guardrail/redaction tests, no external deps
uv run pytest tests/integration -m integration   # requires your Postgres to be reachable
uv run investigation-server    # starts the MCP server over HTTP on port 8000
```

See `.env.example` for every available variable. Each source is independent —
you only need to fill in the block for the source(s) you actually want to use;
the tools for a source you skip will just error at call time instead of
blocking startup.

### What each source needs

| Source | Required | Notes |
|---|---|---|
| **PostgreSQL** (`db_*` tools) | `DB_URL` | Full connection string, `asyncpg` driver: `postgresql+asyncpg://user:pass@host:5432/dbname`. Point this at a read-only role/replica — see [Security model](#security-model). |
| **AWS CloudWatch** (`cw_*` tools) | `CLOUDWATCH_REGION` + one of (`AWS_PROFILE`) or (`CLOUDWATCH_ACCESS_KEY_ID` + `CLOUDWATCH_SECRET_ACCESS_KEY`) + `CLOUDWATCH_ALLOWED_LOG_GROUP` | `CLOUDWATCH_REGION` has no `AWS_REGION` fallback — it must be set explicitly. `CLOUDWATCH_ALLOWED_LOG_GROUP` is a comma-separated allowlist; leaving it unset means no log group is queryable. |
| **New Relic** (`nr_*` tools) | `NEW_RELIC_API_KEY` + `NEW_RELIC_ACCOUNT_ID` | The API key must be a **User API key** (`NRAK-...`), not an ingest/license key. `NEW_RELIC_REGION` defaults to `us`; set it to `eu` for EU-region accounts. |

The server itself (`SERVER_HOST`/`SERVER_PORT`/`SERVER_PATH`) and `PII_REDACTION`
have working defaults out of the box and don't need to be touched for local use.
It starts on `http://127.0.0.1:8000/mcp` by default (streamable HTTP transport)
and has no built-in HTTP authentication, so only set `SERVER_HOST` to a
non-loopback address when access is protected by an authenticated proxy or
equivalent network control.

## Registering with an MCP client

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "investigation": {
      "type":"http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

**Claude Desktop** — same shape in `claude_desktop_config.json`.

## Tool catalog

For a junior-friendly reference with inputs, output structures, examples, and
tool update guidance, see [`docs/tool-reference.md`](docs/tool-reference.md).

| Tool | Purpose |
|---|---|
| `db_list_tables` | Tables + row estimates + comments (cached ~10 min) |
| `db_describe_table` | Columns, types, nullability, PK/FK, indexes |
| `db_sample_rows` | Sample rows, PII-masked |
| `db_explain_query` | `EXPLAIN (FORMAT JSON)` on a validated query |
| `db_run_query` | Validated, read-only `SELECT` execution |
| `db_search_by_identifier` | Find rows across known tables by order/user/payment/request id |
| `cw_list_log_groups` | Log groups + retention + stored bytes |
| `cw_describe_log_fields` | Sample recent events, discover JSON fields + frequency |
| `cw_run_insights_query` | `StartQuery` → poll → `GetQueryResults`, cost-capped |
| `cw_filter_events` | Simple pattern grep over recent events |
| `cw_get_metric_stats` | CloudWatch metric datapoints |
| `nr_list_event_types` | `SHOW EVENT TYPES` — enumerates event types with data (`Log`, `Transaction`, `Span`, `Metric`, custom types) — run before `nr_describe_log_fields` if the event type is unknown |
| `nr_describe_log_fields` | `keyset()` of a New Relic event type (default `Log`), flags likely trace/correlation id attributes — run before writing NRQL |
| `nr_run_nrql_query` | Validated, read-only NRQL execution against New Relic (Log/Metric/event data) |

Resources: `schema://db/tables`, `schema://db/table/{name}`, `logs://groups`,
`docs://query-cookbook`.

## Security model

Every `db_*`/`cw_*`/`nr_*` call goes through a guardrail layer *before* any
network I/O: SQL is parsed with `sqlglot` and rejected unless it's a single
`SELECT`/`WITH...SELECT` with no dangerous functions; CloudWatch calls are
checked against a log-group allowlist, a time-window cap, and a bytes-scanned
cost ceiling; NRQL is restricted to a single `SELECT` with no write-ish
keywords and a clamped `LIMIT`. Results and any log messages are redacted
before returning. See design doc §6 for the full model.

## Testing

- `tests/unit/` — pure logic, no network/DB dependency (the guardrail tests
  are the security-boundary tests and the highest priority in this suite).
- `tests/integration/test_db_tools.py` — runs against your configured
  Postgres; skipped automatically if it isn't reachable.
