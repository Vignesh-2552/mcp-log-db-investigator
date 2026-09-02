# Investigation MCP Server

A [FastMCP](https://gofastmcp.com) server exposing safe, **read-only** tools for
investigating support tickets: schema discovery + query execution against a
Postgres database, and log-group discovery + Logs Insights execution
against CloudWatch. See [`docs/design.md`](docs/design.md) for the
full design.

## Setup

```powershell
uv sync
copy .env.example .env
# Edit .env with your DB_URL and other settings
uv run pytest tests/unit      # guardrail/redaction tests, no external deps
uv run pytest tests/integration -m integration   # requires your Postgres to be reachable
uv run investigation-server    # starts the MCP server over HTTP on port 8000
```

Edit `.env` and set `DB_URL` to your PostgreSQL connection string
(e.g. `postgresql+psycopg://user:pass@host:5432/dbname`).
See `.env.example` for every available variable.

The server starts on `http://127.0.0.1:8000/mcp` by default (streamable HTTP
transport). It does not provide built-in HTTP authentication, so only set
`SERVER_HOST` to a non-loopback address when access is protected by an
authenticated proxy or equivalent network control. Adjust `SERVER_PORT` and
`SERVER_PATH` in `.env` as needed.

CloudWatch tools (`cw_*`) call real `boto3`/AWS APIs; set `CLOUDWATCH_REGION`
(required — there is no `AWS_REGION` fallback), `AWS_PROFILE` (or
`CLOUDWATCH_ACCESS_KEY_ID`/`CLOUDWATCH_SECRET_ACCESS_KEY`), and
`CLOUDWATCH_ALLOWED_LOG_GROUP` to use them.

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

If `MCP_AUTH_TOKEN` is set (see below), add an `Authorization` header:

```json
{
  "mcpServers": {
    "investigation": {
      "type": "http",
      "url": "https://<your-deployment>/mcp",
      "headers": { "Authorization": "Bearer <your MCP_AUTH_TOKEN>" }
    }
  }
}
```

## Deploying (Prefect Horizon)

The streamable-HTTP transport has no authentication of its own at the
application level, so `core/auth.py` adds a bearer-token check
(`MCP_AUTH_TOKEN`) as a baseline — leaving it unset while `SERVER_HOST` is
non-loopback logs a warning at startup and leaves the endpoint open. Keep
`MCP_AUTH_TOKEN` set as defense-in-depth even on platforms with their own
gateway auth (see below).

[Horizon](https://horizon.prefect.io) (from the FastMCP team) deploys
straight from a git repo — no Dockerfile needed:

1. Push this repo to GitHub/GitLab.
2. Horizon dashboard → connect the repo. It detects the Python/FastMCP
   project and builds/containerizes it automatically.
3. Set environment variables from `.env.example` (`DB_URL`, `MCP_AUTH_TOKEN`,
   plus any `CLOUDWATCH_*`/`NEW_RELIC_*` you use) in Horizon's dashboard —
   use different values there than in your local `.env`. **Also set
   `SERVER_HOST=0.0.0.0`** — it defaults to `127.0.0.1` (loopback-only), which
   would make the app unreachable from Horizon's gateway/proxy even though
   the process starts and logs success.
4. Deploy. Horizon's gateway sits in front with its own OAuth 2.1 auth
   (mandatory on the free tier, enforced before requests reach this code) and
   gives you a stable production URL ending in `/mcp`.

Notes:
- Because Horizon's gateway enforces its own OAuth in front, wiring up a
  client (Cursor/Claude Desktop) may follow Horizon's own auth flow rather
  than the static `Authorization: Bearer <MCP_AUTH_TOKEN>` header shown
  above — check Horizon's client-connection docs for the exact shape.
- Verify outbound network access from Horizon's hosted containers to your
  specific Postgres host, AWS region, and New Relic account before relying
  on it — this wasn't confirmed in their public docs at the time of writing.
- Never commit `.env` — it holds real credentials. Set the same variable
  names directly in Horizon's environment-variable UI instead.

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
