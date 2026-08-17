# System Design: Log & Database Investigation MCP Server

**Version:** 0.2
**Status:** As-built (updated from the 0.1 draft to match the current codebase)
**Date:** 17 August 2026

Per-tool inputs, outputs, and examples live in [`docs/tool-reference.md`](tool-reference.md). This document is the architecture and safety model, not a changelog.

---

## 1. Problem Statement

When a client raises a support ticket ("payments failed for 20 minutes yesterday", "this user's order is stuck"), an engineer currently has to:

1. Open the AWS Console, find the right log group, hand-write a CloudWatch Logs Insights query.
2. Open a DB client, hand-write SQL against the read replica to check the record state.
3. Optionally query New Relic (NRQL) for the same window.
4. Manually correlate the sources by `request_id` / `order_id` / timestamp.
5. Repeat 5–10 times as the hypothesis changes.

This is slow, requires tribal knowledge of the schema and log formats, and is only doable by the few engineers who have production access.

## 2. Goal

Build an **MCP (Model Context Protocol) server** using **FastMCP** that exposes safe, **read-only** tools for:

- **Database access** — schema discovery + query execution against Postgres.
- **CloudWatch access** — log group discovery + Logs Insights / FilterLogEvents / metrics.
- **New Relic access** — event-type discovery + NRQL execution via NerdGraph.

The server is connected to **Cursor** and/or **Claude Desktop**, so the engineer can investigate a ticket in natural language, and the model drives the tools to gather evidence and produce a root-cause summary.

### 2.1 Non-Goals (current)

| Out of scope | Reason |
|---|---|
| Any write/DDL/DML to the database | Read-only by design; safety boundary |
| Automatic remediation (restart, redeploy, rollback) | Requires a human approval loop |
| Direct production DB (primary) access | Replica / read-only role only, to protect prod traffic |
| Multi-tenant / customer-facing access | Internal engineering tool only |
| Log ingestion or retention management | Owned by the platform team |
| Server-side cross-source correlation | Client model fans out to each source and merges results (see §4.5) |
| MCP prompts / composite investigation tools | Not implemented; see §10 |

## 3. High-Level Architecture

```
┌──────────────────────────┐
│  Cursor  /  Claude App   │   ← engineer types: "why did order 88213 fail?"
│      (MCP Client)        │
└───────────┬──────────────┘
            │  MCP protocol (streamable HTTP, :8000/mcp)
┌───────────▼──────────────────────────────────────────────────────────┐
│              FastMCP Investigation Server                            │
│                                                                      │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────────────────┐  │
│  │  Tools     │  │  Resources   │  │  Prompts                     │  │
│  │  (thin     │  │  schema://   │  │  (not implemented — see §10) │  │
│  │   wrappers)│  │  logs://     │  │                              │  │
│  │            │  │  docs://     │  │                              │  │
│  └─────┬──────┘  └──────┬───────┘  └──────────────────────────────┘  │
│        │                │                                            │
│  ┌─────▼────────────────▼────────────────────────────────────────┐   │
│  │  Container (composition root)                                 │   │
│  │  DatabaseService / CloudWatchService / NewRelicService        │   │
│  └─────┬─────────────────────┬─────────────────────┬─────────────┘   │
│        │                     │                     │                 │
│  ┌─────▼────────┐     ┌──────▼─────────┐    ┌──────▼──────────┐      │
│  │ DB guardrail │     │ CW guardrail   │    │ NRQL guardrail  │      │
│  │ (sqlglot)    │     │ (allowlist,    │    │ (regex SELECT,  │      │
│  │              │     │  window, bytes)│    │  LIMIT clamp)   │      │
│  └─────┬────────┘     └──────┬─────────┘    └──────┬──────────┘      │
│        │                     │                     │                 │
│  ┌─────▼────────┐     ┌──────▼─────────┐    ┌──────▼──────────┐      │
│  │ SQLAlchemy   │     │ boto3          │    │ httpx NerdGraph │      │
│  │ + asyncpg    │     │ logs/metrics   │    │                 │      │
│  └─────┬────────┘     └──────┬─────────┘    └──────┬──────────┘      │
│        │                     │                     │                 │
│  stderr operational logs (guardrail decisions, errors, bytes scanned)│
└────────┬─────────────────────┼─────────────────────┼─────────────────┘
         │                     │                     │
┌────────▼─────────┐  ┌────────▼──────────────┐  ┌───▼────────────────┐
│ Postgres         │  │ AWS CloudWatch Logs   │  │ New Relic NerdGraph│
│ (read-only role, │  │ + CloudWatch Metrics  │  │ (NRQL)             │
│  session harden) │  │ (read-only IAM)       │  │                    │
└──────────────────┘  └───────────────────────┘  └────────────────────┘
```

### 3.1 Key Design Decision: Who Generates the Query?

There are two options:

| Option | How it works | Verdict |
|---|---|---|
| **A. Server-side generation** | A `generate_sql(question)` tool calls an LLM inside the server to produce SQL. | ❌ Duplicate LLM cost, extra latency, server needs its own API key, loses conversation context. |
| **B. Client-side generation + server-side validation** ✅ | The server exposes **schema** and **log-field metadata** as resources/tools. The client model (Claude/Cursor) writes the query. The server **validates and executes** it. | ✅ Adopted. The model already has the ticket context; the server stays a thin, safety-focused execution layer. |

**We adopt Option B.** "Query generation" in this system means: *the server supplies enough structured context that the client model can reliably generate a correct query, and rejects anything unsafe.*

Skipping the grounding step (`db_describe_table`, `cw_describe_log_fields`, `nr_describe_log_fields`) is the main cause of queries that silently return zero rows because a field name was guessed wrong.

### 3.2 Internal Layering

Layering is strict and one-directional:

```
tools/*  →  service/*  →  integrations/<source>/guardrail.py  →  integrations/<source>/client.py | engine.py
```

| Layer | Role | Rule |
|---|---|---|
| `src/tools/<source>/` | Thin `@mcp.tool()` wrappers | One file per tool, named after the tool. Docstring + parameter schema + one call into the source's service. No business logic. |
| `src/service/` | Business logic | One class per source (`DatabaseService`, `CloudWatchService`, `NewRelicService`), all inheriting `BaseService` for `Settings` and the `ok(data, meta)` envelope. Validate → execute → redact → structured response. |
| `src/integrations/<source>/guardrail.py` | Validate / reject | Pure functions, no I/O. Service methods never execute a user-supplied query without passing it through the guardrail first. |
| `src/integrations/<source>/client.py` or `engine.py` | Network / DB I/O | SQLAlchemy+asyncpg, boto3, httpx NerdGraph. |

**Inventory tools are the one deliberate exception to "every query goes through the guardrail":** `db_list_tables`, `cw_list_log_groups`, and `nr_list_event_types` send a hardcoded, parameter-free (or window-clamped) query straight to the client, because there is no user-supplied query text to validate. `nr_list_event_types` in particular sends `SHOW EVENT TYPES`, which isn't a `SELECT` and would otherwise be rejected by `validate_nrql`.

Cross-tool logic that one source's several tools share (e.g. CloudWatch's `run_insights_query`, used by both `cw_run_insights_query` and `cw_get_trace_events`) is another method on that source's service class — composition over duplicating polling/cost-ceiling logic.

Network/DB clients are **constructor-injected** on `CloudWatchService` (`logs_client_factory` / `metrics_client_factory`) and `NewRelicService` (`run_nrql_fn`). Unit tests pass a fake client into the constructor instead of monkeypatching module state. `DatabaseService` has no such seam: its only DB-hitting tests are integration tests against a real Postgres.

### 3.3 Composition Root and Registration

`src/core/container.py` is the composition root — the one place that constructs services and wires their dependencies. `Container` lazily builds and caches (singleton-scoped, per container instance) `database_service` / `cloudwatch_service` / `newrelic_service`. Tools call `get_container().database_service.list_tables(...)`. Tests that want an isolated object graph construct their own `Container`; `reset_container()` drops the process-wide singleton so the next `get_container()` rebuilds against current `Settings`.

`src/core/app.py` holds the single shared `mcp = FastMCP("investigation-server")` instance. It lives in its own module so `server.py` (the entry point run via `investigation-server` / `-m`) never gets circularly imported.

Registration is import-side-effect based: `src/tools/__init__.py` imports the three source packages; each package's `__init__.py` imports every tool file so the `@mcp.tool()` decorator runs. `src/resources/__init__.py` does the same for `@mcp.resource()`. `server.py` imports both top-level packages before `mcp.run()`. A new tool file must be added to its source package's `__init__.py` or it will never register.

Settings are one `pydantic-settings` object (`src/core/config.py`), loaded once via `@lru_cache def get_settings()`. Tests that change env vars must call `get_settings.cache_clear()` (and, for DB tests, `await reset_engine()`).

## 4. Tool Catalog

All tools return structured JSON. All are read-only. Success is always `{"ok": True, "data": {...}, "meta": {...}}`; failures are `{"ok": False, "error": {rule, message, detail, allowed}}`.

### 4.1 Database Tools

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `db_list_tables` | `schema?: str` | table names + row estimates + comments | Cached ~10 min. Hardcoded catalog query; no user SQL. |
| `db_describe_table` | `table: str` | columns, types, nullability, PK/FK, indexes | The main grounding tool for query generation |
| `db_sample_rows` | `table: str`, `limit: int = 5` | sample rows, PII-masked | Helps the model learn value formats (e.g. status enums) |
| `db_explain_query` | `sql: str` | `EXPLAIN (FORMAT JSON)` plan | Validated first; run before expensive queries |
| `db_run_query` | `sql: str`, `limit: int = 200` | rows, columns, row count, elapsed ms, executed SQL | **Validated** — see §6.1. `LIMIT` injected/clamped server-side. |
| `db_search_by_identifier` | `identifier: str`, `id_type: str` | matching rows across catalog-discovered tables | `id_type` is any column-name-like string, resolved live against the catalog (no hardcoded table list). Caps at 25 targets; tags `live` vs `historical` schemas. |
| `db_resolve_store` | `name_or_domain: str` | `store_id` + candidates, or `ambiguous: true` | Catalog-discovered domain/hostname/store_name-like columns, `ILIKE` match. Never silently picks one of several store ids. |

There is **no application-level table allowlist**. Any table the dedicated read-only DB role can `SELECT` is queryable; the role itself is the last line of defence.

### 4.2 CloudWatch Tools

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `cw_list_log_groups` | `prefix?: str` | log group names, retention, stored bytes | Filtered to `CLOUDWATCH_ALLOWED_LOG_GROUP` when set |
| `cw_describe_log_fields` | `log_group: str` | JSON shapes clustered by top-level key set, field frequencies, correlation-id candidates | Samples recent events + an error/fatal boost so rare error shapes aren't drowned out |
| `cw_run_insights_query` | `log_groups`, `query`, `start`, `end`, `limit: int = 100`, `include_ptr: bool = False` | result rows, bytes scanned, status | Wraps `StartQuery` → poll with backoff → `GetQueryResults`. `limit` is the AWS `StartQuery` cap. `@ptr` stripped unless requested. |
| `cw_get_trace_events` | `log_group`, `field`, `value`, `start`, `end`, `limit: int = 200`, `include_ptr: bool = False` | matching lines sorted chronologically | CloudWatch-only convenience wrapper around Insights. Escapes `value` for safe embedding. Does **not** fan out to DB or New Relic. |
| `cw_filter_events` | `log_group: str`, `pattern: str`, `minutes: int = 30` | raw log events (capped at 1000) | Cheaper than Insights for simple greps |
| `cw_get_metric_stats` | `namespace`, `metric`, `dimensions`, `period`, `start`, `end` | datapoints (avg/sum/min/max/sample count) | For correlating error spikes with CPU/latency |

### 4.3 New Relic Tools

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `nr_list_event_types` | `hours: int = 24` | event types with data in the window | `SHOW EVENT TYPES` — bypasses the SELECT-only NRQL guardrail on purpose |
| `nr_describe_log_fields` | `event_type: str = "Log"`, `hours: int = 1` | `keyset()` fields + likely trace/correlation-id attributes | Grounding tool; guessing NR attribute names is the #1 cause of empty results |
| `nr_run_nrql_query` | `query: str`, `limit: int = 100` | rows, executed query, NerdGraph metadata | **Validated** — see §6.3. `LIMIT` injected/clamped. |

### 4.4 Resources

Read-only context the client can pull without a tool call:

- `schema://db/tables` — compact JSON dump of every table
- `schema://db/table/{name}` — single table detail
- `logs://groups` — log group inventory (reuses `cw_list_log_groups`)
- `docs://query-cookbook` — curated example queries (SQL + Logs Insights + NRQL) from `src/data/query_cookbook.yaml`

The **query cookbook** is a deliberate accuracy lever, not just documentation: giving the client model curated example queries measurably improves generation accuracy over schema alone. When a query pattern proves useful or a source's field-naming quirk causes repeated bad queries, add/update a cookbook entry rather than only fixing it in prose.

### 4.5 Not Implemented

These appeared in the 0.1 draft and are **not** in the codebase. Do not assume they exist.

| Item | Original intent | Current substitute |
|---|---|---|
| `investigation_start` | Ticket id + description + time window → structured investigation plan | The client model plans from the conversation + cookbook |
| `trace_request` | Fan out a `request_id` / `correlation_id` across CloudWatch **and** the DB, return a merged timeline | `cw_get_trace_events` (CloudWatch only) + `db_search_by_identifier` / `nr_run_nrql_query`; the client merges |
| MCP prompts `investigate_incident` / `trace_user_journey` / `slow_endpoint_rca` | Reusable workflows exposed to the client | None |

Cross-source correlation (DB + CloudWatch + New Relic) is entirely manual: the client model fans out to each source's tools and merges results itself, with no server-side guidance beyond the query cookbook.

## 5. Investigation Flow (Sequence)

```
Engineer: "Client says checkout failed around 14:30 IST yesterday for user 4417."

1. Client → db_describe_table("orders")              → schema grounding
2. Client → db_run_query("SELECT ... user_id=4417 AND created_at BETWEEN ...")
                                                     → finds order 88213, status=FAILED
   (or db_search_by_identifier("4417", "user_id") as the convenience path)
3. Client → cw_describe_log_fields("/aws/ecs/checkout-svc")
                                                     → correlation_id_candidates includes order_id
4. Client → cw_get_trace_events(
              log_group=..., field="order_id", value="88213", start=..., end=...)
                                                     → stack trace: payment gateway 502
   (or cw_run_insights_query for a multi-field filter the convenience tool doesn't cover)
5. Client → cw_get_metric_stats(gateway 5xx)         → confirms provider-wide spike
6. Client → nr_describe_log_fields() then nr_run_nrql_query(...)
                                                     → optional second log source
7. Model synthesises RCA + evidence → engineer reviews → replies to client
```

There is no server-side `investigation_start` / `trace_request` step. The client model owns planning and cross-source merge.

## 6. Security Model

This is the section that must not be compromised — the server has production data access.

### 6.1 Database

- **Read-only connection.** `DB_URL` is expected to point at a replica (or at least a read-only role). The application does not enforce replica-vs-primary; that is an ops concern.
- **Dedicated role.** `CREATE ROLE mcp_ro LOGIN; GRANT USAGE ON SCHEMA ... ; GRANT SELECT ON ALL TABLES ...`. No `INSERT/UPDATE/DELETE/CREATE/DROP`. The DB itself is the last line of defence, not the app. There is no `DB_TABLE_ALLOWLIST`.
- **Session hardening** on every new physical connection (`integrations/database/engine.py`):
  - `SET default_transaction_read_only = on;`
  - `SET statement_timeout = '<DB_STATEMENT_TIMEOUT_MS>ms';` (default 15 s)
  - `SET idle_in_transaction_session_timeout = '<DB_IDLE_TXN_TIMEOUT_MS>ms';` (default 30 s)
- **AST validation** before execution using `sqlglot` (`integrations/database/guardrail.py`):
  - Parse the statement. Reject if it fails to parse.
  - Reject if more than one statement (blocks `; DROP TABLE`).
  - Allow only `SELECT` and `WITH ... SELECT` root nodes.
  - Reject any node type in the denylist (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `GRANT`, `TRUNCATE`, `COPY`, `Command`).
  - Reject calls to dangerous functions (`pg_read_file`, `pg_read_binary_file`, `pg_ls_dir`, `pg_sleep`, `dblink`, `dblink_connect`, `lo_import`, `lo_export`, `pg_terminate_backend`, `pg_cancel_backend`).
  - CTE aliases are not treated as real tables when collecting referenced table names.
- **Result limits.** Inject/clamp `LIMIT` to `DB_MAX_ROWS` (default 500); truncate any single cell over `DB_MAX_CELL_BYTES` (default 4 KB).

### 6.2 CloudWatch / AWS

- Dedicated IAM role, least privilege:
  ```
  logs:DescribeLogGroups, logs:DescribeLogStreams,
  logs:StartQuery, logs:GetQueryResults, logs:StopQuery,
  logs:FilterLogEvents, logs:GetLogEvents,
  cloudwatch:GetMetricStatistics, cloudwatch:ListMetrics
  ```
  No `logs:PutLogEvents`, no `logs:DeleteLogGroup`.
- Resource-scoped to `CLOUDWATCH_ALLOWED_LOG_GROUP` (comma-separated). Region always comes from `CLOUDWATCH_REGION` — there is no `AWS_REGION` fallback. Auth is `CLOUDWATCH_ACCESS_KEY_ID`/`CLOUDWATCH_SECRET_ACCESS_KEY`, else `AWS_PROFILE`, else boto3's default credential chain.
- **Cost ceiling.** Logs Insights bills per GB scanned. Enforce: max time window (`CW_MAX_WINDOW_HOURS`, default 7 days; default window `CW_DEFAULT_WINDOW_HOURS` = 24 h), mandatory `limit` on `StartQuery`, abort + report if `bytesScanned` exceeds `CW_MAX_BYTES_SCANNED`. Bytes scanned is always returned in `meta`. On ceiling exceeded, the service also suggests a narrower window from the observed scan rate.
- **Polling.** `poll_query_with_backoff` up to `CW_POLL_MAX_WAIT_S` (default 60 s). If still running, return `query_id` so the caller can continue rather than failing hard.
- **`cw_get_trace_events` injection surface.** Logs Insights has no parameterized-query API. `field` must match a field-name regex; `value` has `\`/`"` escaped and embedded newlines rejected.

### 6.3 New Relic

NRQL has no AST parser comparable to sqlglot, so the guardrail is regex-based (`integrations/newrelic/guardrail.py`):

- Reject empty queries and stacked statements (`;`).
- Require a leading `SELECT`.
- Reject write-ish keywords (`DELETE`, `INSERT`, `UPDATE`, `CREATE`, `DROP`, `ALTER`, `GRANT`, `TRUNCATE`).
- Inject/clamp `LIMIT` to `NR_MAX_ROWS` (default 500).

This is a first line of defence, not as rigorous as the SQL guardrail. It is good enough because the NerdGraph `nrql` field itself only accepts queries, not mutations (alert creation etc. go through other GraphQL operations). Credentials: `NEW_RELIC_API_KEY` (user key, `NRAK-...`) + `NEW_RELIC_ACCOUNT_ID`; region `us` or `eu` selects the GraphQL endpoint.

### 6.4 Data Protection

- **PII redaction** in the response path (`core/redaction.py`), applied at the tool/service boundary, not deeper in the stack: column-name masking (`PII_COLUMN_NAMES`) plus regex patterns (email / card / Aadhaar / PAN / phone) applied to both DB/NRQL row values and raw log message text. UUIDs are protected from the card/Aadhaar patterns so they are not partially redacted. Toggle: `PII_REDACTION` (default on).
- **No secrets in tool arguments.** Credentials come from environment / AWS profile only.
- **Server-side logging** to stderr (`core/logging_config.py`). Operational logs capture guardrail decisions and errors so issues can be debugged without exposing sensitive data as tool results. Stderr is used so a future stdio transport would keep stdout free for JSON-RPC.

### 6.5 Human-in-the-Loop

- All tools are read-only, so auto-approval is acceptable for inventory/grounding tools.
- `db_run_query`, `cw_run_insights_query`, and `nr_run_nrql_query` should be marked as requiring confirmation in the **client** for the first iteration, so the engineer sees the generated query before it runs. The server does not implement a confirmation gate.

## 7. Configuration & Deployment

### 7.1 Transport

The process currently runs **streamable HTTP only** (`server.py` → `mcp.run(transport="streamable-http")`). Defaults: `0.0.0.0:8000/mcp`, overridable via `SERVER_HOST` / `SERVER_PORT` / `SERVER_PATH`.

| Mode | Transport | Status |
|---|---|---|
| Local / team laptop | Streamable HTTP | **Current** — `uv run investigation-server` |
| stdio | `stdio` | Not wired in `server.py` (logging still writes to stderr so it would be compatible) |
| Shared team server with OAuth/bearer | Streamable HTTP + auth | Not implemented |

### 7.2 Environment

See `.env.example` for the full list. The settings object is `core.config.Settings`.

```bash
# Database
DB_URL=postgresql+asyncpg://user:password@host:5432/dbname
DB_MAX_ROWS=500
DB_STATEMENT_TIMEOUT_MS=15000
DB_IDLE_TXN_TIMEOUT_MS=30000
DB_MAX_CELL_BYTES=4096
DB_SCHEMA_CACHE_TTL_S=600
DB_STORE_IDENTIFIER_COLUMNS=domain,domain_name,hostname,host,store_name,slug,subdomain
DB_HISTORICAL_SCHEMA_PREFIXES=migration

# AWS / CloudWatch — CLOUDWATCH_REGION is required (no AWS_REGION fallback)
CLOUDWATCH_REGION=ap-south-1
AWS_PROFILE=mcp-readonly
# CLOUDWATCH_ACCESS_KEY_ID=
# CLOUDWATCH_SECRET_ACCESS_KEY=
CLOUDWATCH_ALLOWED_LOG_GROUP=/aws/ecs/checkout-svc,/aws/ecs/payment-svc
CW_MAX_WINDOW_HOURS=168
CW_DEFAULT_WINDOW_HOURS=24
CW_MAX_BYTES_SCANNED=5000000000
CW_POLL_MAX_WAIT_S=60

# New Relic (NerdGraph / NRQL) — User API key (NRAK-...), not an ingest key
NEW_RELIC_API_KEY=
NEW_RELIC_ACCOUNT_ID=
NEW_RELIC_REGION=us
NR_MAX_WINDOW_HOURS=168
NR_DEFAULT_WINDOW_HOURS=24
NR_MAX_ROWS=500

# Server
SERVER_HOST=0.0.0.0
SERVER_PORT=8000
SERVER_PATH=/mcp
PII_REDACTION=true
LOG_LEVEL=INFO
```

### 7.3 Client Registration

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

**Claude Desktop** — same HTTP shape in `claude_desktop_config.json`.

## 8. Error Handling

Every guardrail raises a `ToolError` subclass (`GuardrailError`, `CWGuardrailError`, `NewRelicGuardrailError` in `core/errors.py`) carrying a machine-readable `rule` plus a `detail` string explaining what to change — never a bare "denied". Service methods catch `ToolError` and convert via `.to_response()`. Source-specific exception families are translated privately on each service (`_sqlalchemy_error_response`, `_aws_error_response`, `_httpx_error_response`) rather than a shared abstract method, since the three sources do not share an exception type.

| Failure | Behaviour |
|---|---|
| SQL / NRQL fails validation | Structured error naming the rule (`parse_error`, `root_not_select`, `blocked_function`, `not_select`, …) and the offending clause. The model can self-correct. |
| Query timeout | `rule=query_timeout`; suggests narrowing the window and using `db_explain_query`. The EXPLAIN plan is **not** auto-attached. |
| Logs Insights still running after poll budget | Return `ok: true` with `status: Running` and `query_id` rather than failing |
| Log group not in allowlist | `rule=log_group_not_allowed` plus the list of allowed groups |
| Bytes-scanned ceiling | `rule=bytes_scanned_ceiling_exceeded`; `meta.bytes_scanned` plus a suggested narrower window when scan rate is known |
| Empty result | Return `rows: []` (and a `note` on inventory/grounding tools when the window was empty) — never fabricate |
| Identifier / store lookup found no catalog columns | `rule=no_matching_tables` / `no_store_identifier_columns` with a hint to describe the schema |

The quality of the error messages directly determines how well the model self-corrects. Treat them as a first-class feature.

## 9. Observability

**Implemented**

- Structured operational logs to stderr: guardrail rejections, query row counts / elapsed ms, AWS/New Relic errors, bytes scanned on Insights calls.
- Bytes scanned always returned in CloudWatch `meta`.
- Benign streamable-HTTP client-disconnect noise from the MCP SDK is filtered so it does not look like a server bug.

**Not implemented**

- Per-tool metrics (call count, p50/p95 latency, error rate) and dashboards.
- A dedicated audit log (JSONL / CloudWatch Logs).
- Tracking median time-to-root-cause per ticket.
- Replica lag in `db_run_query` metadata.

Weekly review of tool usage and guardrail rejections to improve the cookbook remains a process recommendation, not a product feature.

## 10. Delivery Status

| Phase | Scope | Status |
|---|---|---|
| **1** | DB tools + sqlglot guardrails + session hardening | **Done** |
| **2** | CloudWatch tools + query cookbook + PII redaction | **Done** (no MCP prompts) |
| **2b** | New Relic tools + NRQL guardrail | **Done** |
| **2c** | Service layer + `Container` composition root | **Done** |
| **3** | Convenience tools `cw_get_trace_events`, `db_resolve_store`, catalog-driven `db_search_by_identifier` | **Done** (CloudWatch-only trace; not the original cross-source `trace_request`) |
| **4** | Streamable HTTP transport | **Done** (no OAuth/bearer; bind is open HTTP) |
| **5** | Composite `investigation_start` / `trace_request`, MCP prompts, shared-deployment auth, dashboards | **Not started** |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Model generates a plausible but wrong query and the engineer trusts the output | Always return the executed query alongside results (`executed_sql` / `executed_query`); require the RCA to cite row counts and timestamps |
| CloudWatch Insights cost blowout | Byte ceiling, time-window cap, cost logged per call, suggested narrower window on ceiling hit |
| Guardrail bypass via SQL injection in a string literal | AST parsing (not regex), plus read-only DB role and session `default_transaction_read_only` as defence in depth |
| NRQL guardrail weaker than SQL (regex, not AST) | NerdGraph `nrql` field is query-only; LIMIT clamp; inventory tools don't take user NRQL |
| Replica lag causes misleading "record missing" conclusions | Not yet exposed in metadata. `db_search_by_identifier` does warn when matches only exist in historical/migration schemas (`data_freshness_note`). |
| Schema drift breaks generated queries | Schema cache TTL (~10 min); schema is fetched live, never hardcoded. Identifier/store tools resolve columns from the catalog at call time. |
| Guessed CloudWatch / New Relic field names return zero rows | Grounding tools (`cw_describe_log_fields`, `nr_describe_log_fields`) plus cookbook entries that tell the model to call them first |

## 12. Assumptions & Open Questions

**Assumptions confirmed by the current implementation:**

1. Database is **PostgreSQL** (sqlglot dialect `postgres`, asyncpg, `pg_class` catalog queries).
2. Logs are in **AWS CloudWatch Logs** (Logs Insights + FilterLogEvents) and optionally **New Relic** (NRQL via NerdGraph).
3. Server is **Python 3.13+** with FastMCP, SQLAlchemy, sqlglot, boto3, httpx.
4. Primary client is **Cursor** and/or **Claude Desktop**, talking HTTP to `localhost:8000/mcp`.

**Still open / ops-dependent:**

1. Do application logs carry a consistent correlation id that also appears in DB records? `cw_describe_log_fields` / `nr_describe_log_fields` surface likely candidates; they cannot invent a shared id if ingestion never set one.
2. Which log groups are in scope? Configured via `CLOUDWATCH_ALLOWED_LOG_GROUP`.
3. Is client PII present in logs? Redaction is on by default; aggressiveness is a regex + column-name list, not a compliance certification.
4. Compliance constraints — is sending production log content to a model provider permitted? This may force on-prem/Bedrock deployment of the *client*, not of this server.
5. Single-tenant DB, or data segregated per client (schema-per-tenant / `tenant_id` column)? Identifier search is catalog-driven and schema-agnostic; tenant isolation is not a server feature.

---

## Appendix A: Package Map

```
src/
  server.py                 # entry point: streamable HTTP
  core/
    app.py                  # shared FastMCP instance
    container.py            # composition root
    config.py               # pydantic-settings
    errors.py               # ToolError + per-source subclasses
    redaction.py            # PII masking
    logging_config.py
    cache.py                # TTL cache for schema/catalog lookups
  tools/{database,cloudwatch,newrelic}/   # one file per @mcp.tool()
  service/                  # DatabaseService, CloudWatchService, NewRelicService
  integrations/
    database/{engine,guardrail,introspect}.py
    cloudwatch/{client,guardrail}.py
    newrelic/{client,guardrail}.py
  resources/{schema,logs,cookbook}.py
  data/query_cookbook.yaml
```

## Appendix B: Query Cookbook

The live cookbook is `src/data/query_cookbook.yaml`, exposed as `docs://query-cookbook`. It currently covers SQL (failed orders, identifier lookup, store resolution, joins), Logs Insights (trace by order/request id, error rate, latency), and NRQL (`keyset()`, trace by order/request id, error facets). Prefer calling the convenience tools (`db_resolve_store`, `cw_get_trace_events`, `nr_describe_log_fields`) over hand-writing the equivalent query when the tool covers the case.
