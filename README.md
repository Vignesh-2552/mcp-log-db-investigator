<div align="center">

# Investigation MCP Server

[![CI](https://github.com/Vignesh-2552/mcp-log-db-investigator/actions/workflows/ci.yml/badge.svg)](https://github.com/Vignesh-2552/mcp-log-db-investigator/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-blue)
![Read-only](https://img.shields.io/badge/access-read--only-brightgreen)
![MCP](https://img.shields.io/badge/protocol-MCP-orange)

<h3>A <a href="https://gofastmcp.com">FastMCP</a> server exposing safe, read-only tools for investigating support tickets across PostgreSQL, AWS CloudWatch, and New Relic.</h3>

<div class="toc">
  <a href="#overview">Overview</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#configure-your-ai-assistant">Configure Your AI Assistant</a> •
  <a href="#usage-examples">Usage Examples</a> •
  <a href="#mcp-server-api">MCP Server API</a> •
  <a href="#security-model">Security Model</a> •
  <a href="#development">Development</a>
</div>

</div>

## Overview

When a support ticket comes in ("checkout failed for user 4417 around 14:30"),
answering it usually means opening a DB client, the AWS Console, and a logging
platform, hand-writing a query in each, then manually cross-referencing
timestamps and ids across all three. This server turns that into a
conversation with an MCP client.

Features include:

- 🐘 **PostgreSQL** — schema discovery, validated read-only `SELECT`
  execution, PII-masked sample rows, and catalog-driven identifier/store
  lookup (no hardcoded table list).
- ☁️ **AWS CloudWatch** — log group discovery, Logs Insights queries
  (cost-capped by bytes scanned), pattern-based log filtering, and metric
  statistics.
- 🟢 **New Relic** — event type and log field discovery (`keyset()`),
  correlation-id detection, and validated NRQL execution.
- 🛡️ **Guardrails, not trust** — every query is AST-parsed (SQL) or
  pattern-validated (NRQL/CloudWatch) before it touches the network; results
  are PII-redacted before they're returned.

The server does **not** generate queries itself. The client model (Claude
Desktop, Cursor) writes the query, using schema/field-discovery tools and a
curated [query cookbook](src/data/query_cookbook.yaml) this server exposes as
grounding context — the server's job is to make sure the model has enough
context to write a *correct* query, and to validate + execute whatever it
writes safely. See [`docs/design.md`](docs/design.md) (§3.1) for the full
rationale.

Cross-source correlation — matching a `request_id`/`order_id` across the DB
and both log sources — is currently done by the client model itself; there's
no composite "investigate this ticket end-to-end" tool yet (see
[`docs/design.md`](docs/design.md) §4.5 for what's not implemented).

## Architecture

![Architecture: MCP client to Investigation MCP Server, fanning out to PostgreSQL, AWS CloudWatch, and New Relic](docs/architecture.svg)

## Quick Start

### Prerequisites

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/getting-started/installation/)
- Access credentials for whichever source(s) you want to use — see
  [What each source needs](#what-each-source-needs) below. You don't need
  all three; a source you skip just errors at call time instead of blocking
  startup.

### Installation

```powershell
uv sync
copy .env.example .env
# edit .env — fill in DB_URL and/or the CloudWatch/New Relic blocks you need
uv run investigation-server
```

The server starts on `http://127.0.0.1:8000/mcp` by default (streamable HTTP
transport). It has no built-in HTTP authentication, so only set `SERVER_HOST`
to a non-loopback address when access is protected by an authenticated proxy
or equivalent network control.

### Docker

If you'd rather not install Python/`uv` locally, build and run the image
instead:

```powershell
docker build -t investigation-mcp .
docker run --rm -p 8000:8000 --env-file .env -e SERVER_HOST=0.0.0.0 investigation-mcp
```

Two things differ from the local (`uv run`) path:

- `SERVER_HOST` must be overridden to `0.0.0.0` — the default `127.0.0.1`
  would only be reachable from inside the container.
- `AWS_PROFILE` won't work (there's no local AWS config file inside the
  image) — use `CLOUDWATCH_ACCESS_KEY_ID`/`CLOUDWATCH_SECRET_ACCESS_KEY`
  in `.env` instead if you need CloudWatch tools.

### What each source needs

| Source | Required | Notes |
|---|---|---|
| **PostgreSQL** (`db_*` tools) | `DB_URL` | Full connection string, `asyncpg` driver: `postgresql+asyncpg://user:pass@host:5432/dbname`. Point this at a read-only role/replica — see [Security Model](#security-model). |
| **AWS CloudWatch** (`cw_*` tools) | `CLOUDWATCH_REGION` + one of (`AWS_PROFILE`) or (`CLOUDWATCH_ACCESS_KEY_ID` + `CLOUDWATCH_SECRET_ACCESS_KEY`) + `CLOUDWATCH_ALLOWED_LOG_GROUP` | `CLOUDWATCH_REGION` has no `AWS_REGION` fallback — it must be set explicitly. `CLOUDWATCH_ALLOWED_LOG_GROUP` is a comma-separated allowlist; leaving it unset means no log group is queryable. |
| **New Relic** (`nr_*` tools) | `NEW_RELIC_API_KEY` + `NEW_RELIC_ACCOUNT_ID` | The API key must be a **User API key** (`NRAK-...`), not an ingest/license key. `NEW_RELIC_REGION` defaults to `us`; set it to `eu` for EU-region accounts. |

See `.env.example` for every available variable, including tuning knobs
(row limits, time-window caps, timeouts) that all ship with working defaults.

## Configure Your AI Assistant

**Cursor** — `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "investigation": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

**Claude Desktop** — same shape in `claude_desktop_config.json`.

## Usage Examples

### Investigate a failed order
Ask:
> Why did checkout fail for order 88213 around 14:30 IST yesterday?

### Find a user's records across the database
Ask:
> Find every row across the schema that references user_id 4417.

### Trace a request through the logs
Ask:
> Pull the log lines for order_id 88213 from /aws/ecs/checkout-svc for the last hour.

### Cross-check against New Relic
Ask:
> What event types have data in New Relic in the last hour, and what fields does the Log event type have?

## MCP Server API

For a junior-friendly reference with inputs, output structures, examples, and
tool update guidance, see [`docs/tool-reference.md`](docs/tool-reference.md).

| Tool Name | Description |
|---|---|
| `db_list_tables` | Tables + row estimates + comments (cached ~10 min). |
| `db_describe_table` | Columns, types, nullability, PK/FK, indexes. |
| `db_sample_rows` | Sample rows, PII-masked. |
| `db_explain_query` | `EXPLAIN (FORMAT JSON)` on a validated query. |
| `db_run_query` | Validated, read-only `SELECT` execution. |
| `db_search_by_identifier` | Find rows across catalog-discovered tables by order/user/payment/request id. |
| `db_resolve_store` | Resolve a store name/domain to a `store_id`, or return candidates if ambiguous. |
| `cw_list_log_groups` | Log groups + retention + stored bytes. |
| `cw_describe_log_fields` | Sample recent events, discover JSON fields + frequency. |
| `cw_run_insights_query` | `StartQuery` → poll → `GetQueryResults`, cost-capped. |
| `cw_get_trace_events` | Convenience wrapper: matching log lines for a field/value, sorted chronologically. |
| `cw_filter_events` | Simple pattern grep over recent events. |
| `cw_get_metric_stats` | CloudWatch metric datapoints. |
| `nr_list_event_types` | `SHOW EVENT TYPES` — enumerates event types with data (`Log`, `Transaction`, `Span`, `Metric`, custom types); run before `nr_describe_log_fields` if the event type is unknown. |
| `nr_describe_log_fields` | `keyset()` of a New Relic event type (default `Log`), flags likely trace/correlation id attributes; run before writing NRQL. |
| `nr_run_nrql_query` | Validated, read-only NRQL execution against New Relic (Log/Metric/event data). |

Resources: `schema://db/tables`, `schema://db/table/{name}`, `logs://groups`,
`docs://query-cookbook`.

## Security Model

Every `db_*`/`cw_*`/`nr_*` call goes through a guardrail layer *before* any
network I/O: SQL is parsed with `sqlglot` and rejected unless it's a single
`SELECT`/`WITH...SELECT` with no dangerous functions; CloudWatch calls are
checked against a log-group allowlist, a time-window cap, and a bytes-scanned
cost ceiling; NRQL is restricted to a single `SELECT` with no write-ish
keywords and a clamped `LIMIT`. Results and any log messages are redacted
before returning. See [`docs/design.md`](docs/design.md) §6 for the full model.

## Development

```powershell
uv run ruff check .                              # lint
uv run pytest tests/unit -q                      # guardrail/redaction tests, no external deps
uv run pytest tests/integration -m integration   # requires a reachable Postgres; auto-skips otherwise
```

- `tests/unit/` — pure logic, no network/DB dependency (the guardrail tests
  are the security-boundary tests and the highest priority in this suite).
- `tests/integration/test_db_tools.py` — runs against your configured
  Postgres; skipped automatically if it isn't reachable.

CI ([`.github/workflows/ci.yml`](.github/workflows/ci.yml)) runs lint + both
test suites on every push/PR to `main`/`dev`.

See [`CLAUDE.md`](CLAUDE.md) for the architecture/layering conventions this
codebase follows, and [`docs/tool-reference.md`](docs/tool-reference.md) for
how to add a new tool. Contributing a change? See
[`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

[MIT](LICENSE)
