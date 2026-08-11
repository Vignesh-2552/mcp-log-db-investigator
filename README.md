# Investigation MCP Server

A [FastMCP](https://gofastmcp.com) server exposing safe, **read-only** tools for
investigating support tickets: schema discovery + query execution against a
Postgres read replica, and log-group discovery + Logs Insights execution
against CloudWatch. See [`doc/Log_Database.md`](doc/Log_Database.md) for the
full design.

This build covers **Phase 1** (DB tools + guardrails) and **Phase 2**
(CloudWatch tools + prompts + query cookbook) of that design. Out of scope for
now: the `trace_request`/`investigation_start` composite tools, PII-redaction
hardening, and HTTP transport/auth — see doc §10 for the full roadmap.

## Setup

```powershell
uv sync
copy .env.example .env
docker compose up -d          # local Postgres with a sample schema, for dev/testing
uv run pytest tests/unit      # guardrail/redaction/audit tests, no external deps
uv run pytest tests/integration -m integration   # requires docker-compose Postgres above
uv run server.py              # starts the MCP server over stdio
```

The default `.env.example` points at the docker-compose Postgres
(`localhost:55432`, role `mcp_ro`), so the steps above work with zero real
infrastructure. Point `DB_HOST`/`DB_USER`/etc. at a real read replica when
you have one — see `.env.example` for every variable (mirrors design doc §7.2).

CloudWatch tools (`cw_*`) call real `boto3`/AWS APIs; set `AWS_PROFILE` /
`AWS_REGION` and `CW_LOG_GROUP_ALLOWLIST` to use them. There is no local mock
for CloudWatch in this build — those tools are exercised manually once an AWS
profile is available.

## Local Postgres sample data

`docker/postgres/init/` creates `orders` / `users` / `payments` /
`audit_events` tables plus a dedicated read-only `mcp_ro` role (no
INSERT/UPDATE/DELETE/DDL — the DB role is the last line of defence, not just
the app-layer guardrail). Seed data includes the order/user referenced in the
design doc's example investigation flow (order `88213`, user `4417`,
`status = 'FAILED'`, `error_code = 'GATEWAY_502'`).

## Registering with an MCP client

**Cursor** — `.cursor/mcp.json` (already included in this repo, adjust the
path if you clone elsewhere):

```json
{
  "mcpServers": {
    "investigation": {
      "command": "uv",
      "args": ["run", "--directory", "D:/Vignesh/mcp_tool", "server.py"],
      "env": { "AWS_PROFILE": "mcp-readonly" }
    }
  }
}
```

**Claude Desktop** — same shape in `claude_desktop_config.json`.

Secrets (`DB_PASSWORD`, etc.) come from `.env`, not the client config.

## Tool catalog

| Tool | Purpose |
|---|---|
| `db_list_tables` | Allowlisted tables + row estimates + comments (cached ~10 min) |
| `db_describe_table` | Columns, types, nullability, PK/FK, indexes |
| `db_sample_rows` | Sample rows, PII-masked |
| `db_explain_query` | `EXPLAIN (FORMAT JSON)` on a validated query |
| `db_run_query` | Validated, read-only `SELECT` execution |
| `db_search_by_identifier` | Find rows across known tables by order/user/payment/request id |
| `cw_list_log_groups` | Allowlisted log groups + retention + stored bytes |
| `cw_describe_log_fields` | Sample recent events, discover JSON fields + frequency |
| `cw_run_insights_query` | `StartQuery` → poll → `GetQueryResults`, cost-capped |
| `cw_filter_events` | Simple pattern grep over recent events |
| `cw_get_metric_stats` | CloudWatch metric datapoints |

Resources: `schema://db/tables`, `schema://db/table/{name}`, `logs://groups`,
`docs://query-cookbook`. Prompts: `investigate_incident`, `trace_user_journey`,
`slow_endpoint_rca`.

## Security model

Every `db_*`/`cw_*` call goes through a guardrail layer *before* any network
I/O: SQL is parsed with `sqlglot` and rejected unless it's a single
`SELECT`/`WITH...SELECT` referencing only allowlisted tables with no
dangerous functions; CloudWatch calls are checked against a log-group
allowlist, a time-window cap, and a bytes-scanned cost ceiling. Every call
(including rejected ones) is written to `audit.jsonl` with redacted
arguments. See design doc §6 for the full model.

## Testing

- `tests/unit/` — pure logic, no network/DB dependency (the guardrail tests
  are the security-boundary tests and the highest priority in this suite).
- `tests/integration/test_db_tools.py` — runs against the docker-compose
  Postgres; skipped automatically if it isn't reachable.
