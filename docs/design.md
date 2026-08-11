# System Design: Log & Database Investigation MCP Server

**Version:** 0.1 (Draft)
**Status:** For review
**Author:** _(you)_
**Date:** 10 August 2026

---

## 1. Problem Statement

When a client raises a support ticket ("payments failed for 20 minutes yesterday", "this user's order is stuck"), an engineer currently has to:

1. Open the AWS Console, find the right log group, hand-write a CloudWatch Logs Insights query.
2. Open a DB client, hand-write SQL against the read replica to check the record state.
3. Manually correlate the two by `request_id` / `order_id` / timestamp.
4. Repeat 5–10 times as the hypothesis changes.

This is slow, requires tribal knowledge of the schema and log formats, and is only doable by the few engineers who have production access.

## 2. Goal

Build an **MCP (Model Context Protocol) server** using **FastMCP** that exposes safe, **read-only** tools for:

- **Database access** — schema discovery + query execution against a read replica.
- **CloudWatch access** — log group discovery + Logs Insights query execution.

The server is connected to **Cursor** and/or **Claude Desktop**, so the engineer can investigate a ticket in natural language, and the model drives the tools to gather evidence and produce a root-cause summary.

### 2.1 Non-Goals (v1)

| Out of scope | Reason |
|---|---|
| Any write/DDL/DML to the database | Read-only by design; safety boundary |
| Automatic remediation (restart, redeploy, rollback) | Requires human approval loop |
| Direct production DB (primary) access | Replica only, to protect prod traffic |
| Multi-tenant / customer-facing access | Internal engineering tool only |
| Log ingestion or retention management | Owned by the platform team |

## 3. High-Level Architecture

```
┌──────────────────────────┐
│  Cursor  /  Claude App   │   ← engineer types: "why did order 88213 fail?"
│      (MCP Client)        │
└───────────┬──────────────┘
            │  MCP protocol (stdio | streamable HTTP)
┌───────────▼──────────────────────────────────────────────┐
│              FastMCP Investigation Server                │
│                                                          │
│  ┌────────────┐  ┌──────────────┐  ┌──────────────────┐  │
│  │  Tools     │  │  Resources   │  │  Prompts         │  │
│  │  layer     │  │  (schema,    │  │  (investigate_   │  │
│  │            │  │   log groups)│  │   incident, ...) │  │
│  └─────┬──────┘  └──────┬───────┘  └──────────────────┘  │
│        │                │                                │
│  ┌─────▼────────────────▼───────────────────────────┐    │
│  │        Guardrail / Validation Layer              │    │
│  │  • SQL AST check (SELECT-only)                   │    │
│  │  • table & log-group allowlist                   │    │
│  │  • LIMIT injection, statement timeout            │    │
│  │  • PII redaction, cost ceiling, rate limit       │    │
│  └─────┬────────────────────────────┬───────────────┘    │
│        │                            │                    │
│  ┌─────▼────────┐          ┌────────▼────────┐           │
│  │ DB Adapter   │          │ CloudWatch      │           │
│  │ (SQLAlchemy) │          │ Adapter (boto3) │           │
│  └─────┬────────┘          └────────┬────────┘           │
│        │                            │                    │
│  ┌─────▼────────────────────────────▼───────────────┐    │
│  │              Audit Log (JSONL / CW Logs)         │    │
│  └──────────────────────────────────────────────────┘    │
└────────┬──────────────────────────────┬──────────────────┘
         │                              │
┌────────▼─────────┐          ┌─────────▼──────────────┐
│ Postgres         │          │ AWS CloudWatch Logs    │
│ READ REPLICA     │          │ (Logs Insights)        │
│ (read-only role) │          │ (read-only IAM role)   │
└──────────────────┘          └────────────────────────┘
```

### 3.1 Key Design Decision: Who Generates the Query?

There are two options:

| Option | How it works | Verdict |
|---|---|---|
| **A. Server-side generation** | A `generate_sql(question)` tool calls an LLM inside the server to produce SQL. | ❌ Duplicate LLM cost, extra latency, server needs its own API key, loses conversation context. |
| **B. Client-side generation + server-side validation** ✅ | The server exposes **schema** and **log-field metadata** as resources/tools. The client model (Claude/Cursor) writes the query. The server **validates and executes** it. | ✅ Recommended. The model already has the ticket context; the server stays a thin, safety-focused execution layer. |

**We adopt Option B.** "Query generation" in this system means: *the server supplies enough structured context that the client model can reliably generate a correct query, and rejects anything unsafe.*

## 4. Tool Catalog

All tools return structured JSON. All are read-only.

### 4.1 Database Tools

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `db_list_tables` | `schema?: str` | table names + row estimates + comments | Cached 10 min |
| `db_describe_table` | `table: str` | columns, types, nullability, PK/FK, indexes | The main "grounding" tool for query generation |
| `db_sample_rows` | `table: str`, `limit: int = 5` | sample rows, PII-masked | Helps the model learn value formats (e.g. status enums) |
| `db_explain_query` | `sql: str` | `EXPLAIN` plan | Run before expensive queries |
| `db_run_query` | `sql: str`, `limit: int = 200` | rows, column names, row count, elapsed ms | **Validated** — see §6 |
| `db_search_by_identifier` | `identifier: str`, `id_type: str` | matching rows across known tables | Convenience wrapper for the common "find order X" flow |

### 4.2 CloudWatch Tools

| Tool | Arguments | Returns | Notes |
|---|---|---|---|
| `cw_list_log_groups` | `prefix?: str` | log group names, retention, stored bytes | Filtered by allowlist |
| `cw_describe_log_fields` | `log_group: str` | discovered fields + frequency | Grounds Logs Insights query generation |
| `cw_run_insights_query` | `log_groups: list[str]`, `query: str`, `start: str`, `end: str`, `limit: int = 100` | result rows, bytes scanned, status | Wraps `StartQuery` → poll → `GetQueryResults` into one blocking call |
| `cw_filter_events` | `log_group: str`, `pattern: str`, `minutes: int = 30` | raw log events | Cheaper than Insights for simple greps |
| `cw_get_metric_stats` | `namespace`, `metric`, `dimensions`, `period`, `start`, `end` | datapoints | For correlating error spikes with CPU/latency |

### 4.3 Investigation Tools (Composite)

| Tool | Purpose |
|---|---|
| `investigation_start` | Takes ticket id + description + time window. Returns a structured investigation plan. |
| `trace_request` | Given a `request_id` / `correlation_id`, fans out to CloudWatch across all service log groups **and** the DB, returns a merged, time-ordered timeline. This is the highest-value tool — it's the manual step engineers repeat most. |

### 4.4 Resources & Prompts

**Resources** (read-only context the client can pull without a tool call):
- `schema://db/tables` — full compact schema dump
- `schema://db/table/{name}` — single table detail
- `logs://groups` — log group inventory with service mapping
- `docs://query-cookbook` — curated example queries (SQL + Logs Insights) for common investigations

**Prompts** (reusable workflows exposed to the client):
- `investigate_incident(ticket_id, description, time_window)`
- `trace_user_journey(user_id, date)`
- `slow_endpoint_rca(endpoint, time_window)`

The **query cookbook** is important: giving the model 10–15 verified example queries dramatically improves generation accuracy versus schema alone.

## 5. Investigation Flow (Sequence)

```
Engineer: "Client says checkout failed around 14:30 IST yesterday for user 4417."

1. Client → investigation_start(...)          → plan + context_id
2. Client → db_describe_table("orders")       → schema grounding
3. Client → db_run_query("SELECT ... user_id=4417 AND created_at BETWEEN ...")
                                              → finds order 88213, status=FAILED
4. Client → cw_describe_log_fields("/aws/ecs/checkout-svc")
5. Client → cw_run_insights_query(
              query="fields @timestamp,@message | filter order_id='88213' | sort @timestamp asc")
                                              → stack trace: payment gateway 502
6. Client → cw_get_metric_stats(gateway 5xx)  → confirms provider-wide spike
7. Client → trace_request("req-9f2c...")      → merged timeline
8. Model synthesises RCA + evidence → engineer reviews → replies to client
```

## 6. Security Model

This is the section that must not be compromised — the server has production data access.

### 6.1 Database

- **Replica only.** Connection string points at a read replica, never the primary.
- **Dedicated role.** `CREATE ROLE mcp_ro LOGIN; GRANT USAGE ON SCHEMA ... ; GRANT SELECT ON ALL TABLES ...`. No `INSERT/UPDATE/DELETE/CREATE/DROP`. The DB itself is the last line of defence, not the app.
- **Session hardening** on every connection:
  - `SET default_transaction_read_only = on;`
  - `SET statement_timeout = '15s';`
  - `SET idle_in_transaction_session_timeout = '30s';`
- **AST validation** before execution using `sqlglot`:
  - Parse the statement. Reject if it fails to parse.
  - Reject if more than one statement (blocks `; DROP TABLE`).
  - Allow only `SELECT` and `WITH ... SELECT` root nodes.
  - Reject any node type in the denylist (`INSERT`, `UPDATE`, `DELETE`, `MERGE`, `CREATE`, `DROP`, `ALTER`, `GRANT`, `TRUNCATE`, `COPY`, `CALL`, `DO`).
  - Reject calls to dangerous functions (`pg_read_file`, `pg_sleep`, `dblink`, `lo_import`).
  - Extract referenced tables; reject anything not in the **table allowlist**.
- **Result limits.** Inject/clamp `LIMIT` to a max of 500 rows; truncate any single cell over 4 KB.

### 6.2 CloudWatch / AWS

- Dedicated IAM role, least privilege:
  ```
  logs:DescribeLogGroups, logs:DescribeLogStreams,
  logs:StartQuery, logs:GetQueryResults, logs:StopQuery,
  logs:FilterLogEvents, logs:GetLogEvents,
  cloudwatch:GetMetricStatistics, cloudwatch:ListMetrics
  ```
  No `logs:PutLogEvents`, no `logs:DeleteLogGroup`.
- Resource-scoped to an allowlist of log group ARNs.
- **Cost ceiling.** Logs Insights bills per GB scanned. Enforce: max time window (default 24 h, hard cap 7 days), mandatory `limit`, and abort + report if `bytesScanned` exceeds a threshold. Log the scanned bytes on every call so cost is visible.

### 6.3 Data Protection

- **PII redaction** in the response path: regex + column-name-based masking for email, phone, PAN/Aadhaar, card numbers, auth tokens. Applied to both DB rows and log messages before they leave the server.
- **No secrets in tool arguments.** Credentials come from environment / AWS profile / Secrets Manager only.
- **Server-side logging.** Operational log messages capture guardrail decisions and errors so issues can be debugged without exposing sensitive data to the client.

### 6.4 Human-in-the-Loop

- All tools are read-only, so auto-approval is acceptable for `db_describe_table`, `cw_list_log_groups`, etc.
- `db_run_query` and `cw_run_insights_query` should be marked as requiring confirmation in the client for the first iteration, so the engineer sees the generated query before it runs. Relax once trust is established.

## 7. Configuration & Deployment

### 7.1 Transport

| Mode | Transport | When |
|---|---|---|
| Local dev / single engineer | `stdio` | Phase 1 — simplest, credentials stay on the laptop |
| Shared team server | Streamable HTTP + OAuth/bearer | Phase 3 — shared deployment, no local prod creds |

Start with **stdio**. Move to HTTP only when more than ~3 engineers need it.

### 7.2 Environment

```bash
# Database
DB_HOST=replica.internal
DB_PORT=5432
DB_NAME=appdb
DB_USER=mcp_ro
DB_PASSWORD=<from secrets manager>
DB_TABLE_ALLOWLIST=public.orders,public.users,public.payments,public.audit_events
DB_MAX_ROWS=500
DB_STATEMENT_TIMEOUT_MS=15000

# AWS
AWS_REGION=ap-south-1
AWS_PROFILE=mcp-readonly
CW_LOG_GROUP_ALLOWLIST=/aws/ecs/checkout-svc,/aws/ecs/payment-svc,/aws/lambda/webhook
CW_MAX_WINDOW_HOURS=168
CW_MAX_BYTES_SCANNED=5000000000

# Server
PII_REDACTION=on
```

### 7.3 Client Registration

**Cursor** — `.cursor/mcp.json`:
```json
{
  "mcpServers": {
    "investigation": {
      "command": "uv",
      "args": ["run", "--directory", "/opt/mcp-investigation", "server.py"],
      "env": { "AWS_PROFILE": "mcp-readonly" }
    }
  }
}
```

**Claude Desktop** — same shape in `claude_desktop_config.json`.

## 8. Error Handling

| Failure | Behaviour |
|---|---|
| SQL fails validation | Return a **structured error naming the rule broken** and the offending clause. The model can then self-correct. Do not return a bare "denied". |
| Query timeout | Return timeout + the `EXPLAIN` plan, suggest narrowing the time window |
| Logs Insights still running | Poll with backoff up to 60 s, then return the query id so it can be polled again rather than failing |
| Log group not in allowlist | Return the error plus the list of allowed groups |
| Empty result | Return `rows: []` with an explicit `"no matching records"` note — never fabricate |

The quality of the error messages directly determines how well the model self-corrects. Treat them as a first-class feature.

## 9. Observability

- Per-tool metrics: call count, p50/p95 latency, error rate, rows returned, bytes scanned.
- Weekly review of tool usage and guardrail rejections to improve future query quality (feeds the cookbook).
- Track the real outcome metric: **median time-to-root-cause per ticket**, before vs after.

## 10. Delivery Phases

| Phase | Scope | Exit criteria |
|---|---|---|
| **1** | DB tools + guardrails, stdio, local Cursor | Engineer resolves one real ticket end-to-end |
| **2** | CloudWatch tools + prompts + cookbook | Cross-source correlation working |
| **3** | `trace_request` composite tool, PII redaction hardening | Timeline output accepted by the support team |
| **4** | HTTP transport, auth, shared deployment, dashboards | 3+ engineers using it daily |

## 11. Risks

| Risk | Mitigation |
|---|---|
| Model generates a plausible but wrong query and the engineer trusts the output | Always return the executed query alongside results; require the RCA to cite row counts and timestamps |
| CloudWatch Insights cost blowout | Byte ceiling, time-window cap, cost logged per call |
| Guardrail bypass via SQL injection in a string literal | AST parsing (not regex), plus read-only DB role as defence in depth |
| Replica lag causes misleading "record missing" conclusions | Expose replica lag in `db_run_query` metadata |
| Schema drift breaks generated queries | Short schema cache TTL; schema is fetched live, never hardcoded |

## 12. Assumptions & Open Questions

**Assumptions made in this draft — please confirm or correct:**

1. Database is **PostgreSQL** with an available read replica.
2. Logs are in **AWS CloudWatch Logs** and queryable via Logs Insights (i.e. structured/JSON logs, not plain text).
3. Server is written in **Python** with FastMCP.
4. Primary client is **Cursor** and/or **Claude Desktop**, run locally by engineers.
5. Logs contain a **correlation/request id** that also appears in DB records — this is what makes `trace_request` possible.

**Open questions:**

1. Which DB engine and version? (MySQL/Oracle/SQL Server changes the AST validation library and session settings.)
2. Do logs carry a consistent correlation id? If not, this is a prerequisite work item.
3. Which log groups and tables are in scope for v1?
4. Is client PII present in logs? Determines how aggressive redaction must be.
5. Compliance constraints — is sending production log content to a model provider permitted? This may force on-prem/Bedrock deployment.
6. Single-tenant DB, or is data segregated per client (schema-per-tenant / tenant_id column)?

---

## Appendix A: Minimal FastMCP Skeleton

```python
from fastmcp import FastMCP
import sqlglot
from sqlglot import exp

mcp = FastMCP("investigation-server")

BLOCKED = (exp.Insert, exp.Update, exp.Delete, exp.Create,
           exp.Drop, exp.Alter, exp.Grant, exp.TruncateTable)


def validate_sql(sql: str, allowlist: set[str]) -> str:
    statements = sqlglot.parse(sql, read="postgres")
    if len(statements) != 1:
        raise ValueError("Exactly one statement allowed.")
    tree = statements[0]
    if not isinstance(tree, (exp.Select, exp.With)):
        raise ValueError("Only SELECT / WITH...SELECT is permitted.")
    for node in tree.walk():
        if isinstance(node, BLOCKED):
            raise ValueError(f"Blocked operation: {type(node).__name__}")
    for table in tree.find_all(exp.Table):
        name = f"{table.db or 'public'}.{table.name}"
        if name not in allowlist:
            raise ValueError(f"Table not allowed: {name}. Allowed: {sorted(allowlist)}")
    return tree.sql(dialect="postgres")


@mcp.tool()
def db_run_query(sql: str, limit: int = 200) -> dict:
    """Run a read-only SELECT against the replica. Returns rows and metadata."""
    safe_sql = validate_sql(sql, ALLOWLIST)
    ...  # execute with statement_timeout, clamp limit, redact


@mcp.tool()
def cw_run_insights_query(
    log_groups: list[str], query: str, start: str, end: str, limit: int = 100
) -> dict:
    """Run a CloudWatch Logs Insights query and wait for results."""
    ...  # StartQuery -> poll GetQueryResults -> redact


if __name__ == "__main__":
    mcp.run()
```

## Appendix B: Query Cookbook Seed

**SQL**
```sql
-- Failed orders in a window
SELECT id, user_id, status, error_code, created_at
FROM public.orders
WHERE created_at BETWEEN :start AND :end AND status = 'FAILED'
ORDER BY created_at DESC LIMIT 100;
```

**CloudWatch Logs Insights**
```
fields @timestamp, level, service, order_id, @message
| filter order_id = "88213"
| sort @timestamp asc
| limit 200
```

```
filter level = "ERROR"
| stats count() as errors by bin(5m), error_code
| sort errors desc
```
