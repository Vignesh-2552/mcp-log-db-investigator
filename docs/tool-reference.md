# MCP Tool Reference

This document explains every MCP tool exposed by the Investigation MCP Server.
It is written for engineers who need to use, debug, or update the tools.

## Common Response Shape

All tools return a dictionary with this general structure:

```json
{
  "ok": true,
  "data": {},
  "meta": {}
}
```

When a guardrail or external service error happens, tools return:

```json
{
  "ok": false,
  "error": {
    "rule": "machine_readable_rule",
    "message": "Human-readable error message.",
    "detail": "Optional extra detail.",
    "allowed": ["optional", "allowed", "values"]
  }
}
```

Important behavior:

- Database and New Relic result rows are PII-redacted before returning.
- CloudWatch log messages are PII-redacted before returning.
- Database query limits are injected or clamped server-side.
- CloudWatch log groups must pass the configured allowlist.
- Errors are intentionally structured so the client model can self-correct.

## Database Tools

### `db_list_tables`

Lists database tables with row estimates and comments.

Inputs:

```json
{
  "schema": "public"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `schema` | `string | null` | No | `null` | Optional schema filter. If omitted, lists tables from all non-system schemas. |

Success output:

```json
{
  "ok": true,
  "data": {
    "tables": [
      {
        "table": "public.orders",
        "row_estimate": 12345,
        "comment": "Customer orders"
      }
    ]
  },
  "meta": {
    "row_count": 1
  }
}
```

Implementation:

- Tool: `src/tools/database/db_list_tables.py`
- Logic: `src/integrations/database/introspect.py`
- Cached for about 10 minutes.

### `db_describe_table`

Describes a table's columns, primary key, foreign keys, and indexes.

Inputs:

```json
{
  "table": "public.orders"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `table` | `string` | Yes | N/A | Table name. Can be schema-qualified, for example `public.orders`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "table": "public.orders",
    "columns": [
      {
        "name": "id",
        "type": "INTEGER",
        "nullable": false,
        "default": null
      }
    ],
    "primary_key": ["id"],
    "foreign_keys": [
      {
        "columns": ["user_id"],
        "references_table": "public.users",
        "references_columns": ["id"]
      }
    ],
    "indexes": [
      {
        "name": "ix_orders_created_at",
        "columns": ["created_at"],
        "unique": false
      }
    ]
  },
  "meta": {}
}
```

Use this before writing SQL against an unfamiliar table.

### `db_sample_rows`

Returns a small sample of rows from a table. PII fields are redacted.

Inputs:

```json
{
  "table": "public.orders",
  "limit": 5
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `table` | `string` | Yes | N/A | Table name to sample. |
| `limit` | `integer` | No | `5` | Number of rows to return. Clamped by `DB_MAX_ROWS`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "table": "public.orders",
    "columns": ["id", "status", "email"],
    "rows": [
      {
        "id": 88213,
        "status": "FAILED",
        "email": "[REDACTED]"
      }
    ],
    "row_count": 1
  },
  "meta": {
    "row_count": 1
  }
}
```

### `db_explain_query`

Runs `EXPLAIN (FORMAT JSON)` for a validated read-only SQL query.

Inputs:

```json
{
  "sql": "SELECT id, status FROM public.orders WHERE created_at >= now() - interval '1 day'"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `sql` | `string` | Yes | N/A | A single `SELECT` or `WITH ... SELECT` query. |

Success output:

```json
{
  "ok": true,
  "data": {
    "executed_sql": "EXPLAIN (FORMAT JSON) SELECT ... LIMIT 500",
    "plan": []
  },
  "meta": {}
}
```

Guardrails:

- Only one statement is allowed.
- Only `SELECT` and `WITH ... SELECT` are allowed.
- DDL/DML commands are rejected.
- Dangerous Postgres functions are rejected.
- A `LIMIT` is injected or clamped.

### `db_run_query`

Runs a validated read-only SQL query and returns rows.

Inputs:

```json
{
  "sql": "SELECT id, status FROM public.orders ORDER BY created_at DESC",
  "limit": 100
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `sql` | `string` | Yes | N/A | A single `SELECT` or `WITH ... SELECT` query. |
| `limit` | `integer` | No | `200` | Requested row limit. Clamped by `DB_MAX_ROWS`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "rows": [
      {
        "id": 88213,
        "status": "FAILED"
      }
    ],
    "columns": ["id", "status"],
    "row_count": 1,
    "elapsed_ms": 12.34,
    "executed_sql": "SELECT id, status FROM public.orders ORDER BY created_at DESC LIMIT 100"
  },
  "meta": {
    "row_count": 1
  }
}
```

Use this for normal database investigation after checking schema with
`db_describe_table`.

### `db_search_by_identifier`

Searches for rows matching an identifier across discovered database tables.

Inputs:

```json
{
  "identifier": "88213",
  "id_type": "order_id"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `identifier` | `string` | Yes | N/A | The value to search for. |
| `id_type` | `string` | Yes | N/A | Column-name-like identifier type, for example `order_id`, `user_id`, or `transaction_id`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "identifier": "88213",
    "id_type": "order_id",
    "searched_tables": [
      {
        "table": "public.orders",
        "source_type": "live"
      }
    ],
    "truncated": false,
    "matches": [
      {
        "table": "public.orders",
        "column": "id",
        "skipped": false,
        "rows": [],
        "row_count": 1,
        "source_type": "live"
      }
    ],
    "skipped": [],
    "data_freshness_note": null
  },
  "meta": {
    "row_count": 1
  }
}
```

Behavior:

- Finds tables with a matching column name.
- Also checks likely entity tables, for example `order_id` can search `order` and `orders`.
- Tags results as `live` or `historical` based on configured historical schema prefixes.
- Limits fan-out to a fixed maximum number of targets.

### `db_resolve_store`

Resolves a store name or domain to a store ID by searching discovered
store/domain-like columns.

Inputs:

```json
{
  "name_or_domain": "olallawines.com"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `name_or_domain` | `string` | Yes | N/A | Store name, hostname, domain, slug, or similar value. |

Success output:

```json
{
  "ok": true,
  "data": {
    "name_or_domain": "olallawines.com",
    "searched_columns": ["public.stores.domain"],
    "truncated": false,
    "ambiguous": false,
    "store_id": 42,
    "candidates": [
      {
        "table": "public.stores",
        "matched_column": "domain",
        "store_id": 42,
        "row": {
          "id": 42,
          "domain": "olallawines.com"
        }
      }
    ],
    "skipped": [],
    "note": null
  },
  "meta": {
    "candidate_count": 1
  }
}
```

Important:

- Always check `ambiguous`.
- If `ambiguous` is `true`, do not assume `store_id` is safe to use.
- Candidate columns are configured with `DB_STORE_IDENTIFIER_COLUMNS`.

## CloudWatch Tools

### `cw_list_log_groups`

Lists CloudWatch log groups visible to the configured AWS credentials,
filtered by the configured allowlist.

Inputs:

```json
{
  "prefix": "/aws/ecs/"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `prefix` | `string | null` | No | `null` | Optional CloudWatch log group prefix filter. |

Success output:

```json
{
  "ok": true,
  "data": {
    "log_groups": [
      {
        "log_group": "/aws/ecs/checkout-svc",
        "retention_days": 30,
        "stored_bytes": 123456789
      }
    ]
  },
  "meta": {
    "row_count": 1
  }
}
```

### `cw_describe_log_fields`

Samples recent CloudWatch log events and discovers JSON field shapes.

Inputs:

```json
{
  "log_group": "/aws/ecs/checkout-svc"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `log_group` | `string` | Yes | N/A | Allowed CloudWatch log group name. |

Success output:

```json
{
  "ok": true,
  "data": {
    "log_group": "/aws/ecs/checkout-svc",
    "sampled_events": 50,
    "parsed_as_json": 45,
    "sample_composition": {
      "random": 50,
      "error_boosted": 10
    },
    "shapes": [
      {
        "shape_id": "a1b2c3d4",
        "top_level_keys": ["level", "message", "order_id"],
        "row_count": 20,
        "sample_composition": {
          "random": 18,
          "error_boosted": 2
        },
        "fields": [
          {
            "field": "order_id",
            "frequency": 20
          }
        ],
        "example_event": "{\"order_id\":\"88213\"}",
        "correlation_id_candidates": ["order_id"]
      }
    ],
    "note": null
  },
  "meta": {
    "shape_count": 1,
    "sampled_events": 60
  }
}
```

Use this before writing Logs Insights queries. It helps identify real field
names such as `request_id`, `trace_id`, `order_id`, or nested JSON paths.

### `cw_run_insights_query`

Runs a CloudWatch Logs Insights query and waits for results.

Inputs:

```json
{
  "log_groups": ["/aws/ecs/checkout-svc"],
  "query": "fields @timestamp, @message | sort @timestamp desc",
  "start": "2026-08-12T10:00:00+00:00",
  "end": "2026-08-12T11:00:00+00:00",
  "limit": 100,
  "include_ptr": false
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `log_groups` | `list[string]` | Yes | N/A | Allowed log groups to query. |
| `query` | `string` | Yes | N/A | Logs Insights query string. |
| `start` | `string` | Yes | N/A | ISO-8601 start timestamp. |
| `end` | `string` | Yes | N/A | ISO-8601 end timestamp. |
| `limit` | `integer` | No | `100` | Row cap sent to AWS `StartQuery`. |
| `include_ptr` | `boolean` | No | `false` | Include AWS `@ptr` field when true. |

Success output:

```json
{
  "ok": true,
  "data": {
    "status": "Complete",
    "query_id": "abc-123",
    "rows": [
      {
        "@timestamp": "2026-08-12 10:01:00.000",
        "@message": "payment failed"
      }
    ],
    "row_count": 1,
    "bytes_scanned": 123456,
    "records_matched": 1,
    "ptr_included": false
  },
  "meta": {
    "row_count": 1,
    "bytes_scanned": 123456
  }
}
```

If the query is still running, the tool returns `status: "Running"` and a
`query_id`.

### `cw_get_trace_events`

Builds and runs a safe trace lookup query for one field/value pair.

Inputs:

```json
{
  "log_group": "/aws/ecs/checkout-svc",
  "field": "order_id",
  "value": "88213",
  "start": "2026-08-12T10:00:00+00:00",
  "end": "2026-08-12T11:00:00+00:00",
  "limit": 200,
  "include_ptr": false
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `log_group` | `string` | Yes | N/A | Allowed log group name. |
| `field` | `string` | Yes | N/A | Logs Insights field name, for example `request_id` or `message.trace_id`. |
| `value` | `string` | Yes | N/A | Exact value to match. |
| `start` | `string` | Yes | N/A | ISO-8601 start timestamp. |
| `end` | `string` | Yes | N/A | ISO-8601 end timestamp. |
| `limit` | `integer` | No | `200` | Maximum rows. |
| `include_ptr` | `boolean` | No | `false` | Include AWS `@ptr` field when true. |

Success output:

Same base shape as `cw_run_insights_query`, plus:

```json
{
  "log_group": "/aws/ecs/checkout-svc",
  "field": "order_id",
  "value": "88213",
  "executed_query": "fields @timestamp, @message | filter order_id = \"88213\" | sort @timestamp asc"
}
```

Use this after `cw_describe_log_fields` identifies the correct correlation
field.

### `cw_filter_events`

Filters raw CloudWatch log events using a simple filter pattern.

Inputs:

```json
{
  "log_group": "/aws/ecs/checkout-svc",
  "pattern": "ERROR",
  "minutes": 30
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `log_group` | `string` | Yes | N/A | Allowed log group name. |
| `pattern` | `string` | Yes | N/A | CloudWatch filter pattern. |
| `minutes` | `integer` | No | `30` | Lookback window in minutes. |

Success output:

```json
{
  "ok": true,
  "data": {
    "log_group": "/aws/ecs/checkout-svc",
    "events": [
      {
        "timestamp": 1786538460000,
        "message": "ERROR payment failed",
        "log_stream": "checkout/abc"
      }
    ],
    "event_count": 1
  },
  "meta": {
    "row_count": 1
  }
}
```

This is cheaper than Logs Insights for simple searches.

### `cw_get_metric_stats`

Returns CloudWatch metric datapoints for a time window.

Inputs:

```json
{
  "namespace": "AWS/ECS",
  "metric": "CPUUtilization",
  "dimensions": {
    "ClusterName": "prod",
    "ServiceName": "checkout-svc"
  },
  "period": 300,
  "start": "2026-08-12T10:00:00+00:00",
  "end": "2026-08-12T11:00:00+00:00"
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `namespace` | `string` | Yes | N/A | CloudWatch metric namespace. |
| `metric` | `string` | Yes | N/A | Metric name. |
| `dimensions` | `object` | Yes | N/A | Dimension name/value pairs. |
| `period` | `integer` | Yes | N/A | Period in seconds. |
| `start` | `string` | Yes | N/A | ISO-8601 start timestamp. |
| `end` | `string` | Yes | N/A | ISO-8601 end timestamp. |

Success output:

```json
{
  "ok": true,
  "data": {
    "namespace": "AWS/ECS",
    "metric": "CPUUtilization",
    "datapoints": [
      {
        "Timestamp": "2026-08-12T10:05:00+00:00",
        "Average": 55.2,
        "Maximum": 70.1,
        "Minimum": 40.0,
        "Sum": 165.6,
        "SampleCount": 3
      }
    ]
  },
  "meta": {
    "row_count": 1
  }
}
```

## New Relic Tools

### `nr_list_event_types`

Lists New Relic event types with data in the configured account.

Inputs:

```json
{
  "hours": 24
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `hours` | `integer` | No | `24` | Lookback window. Clamped by `NR_MAX_WINDOW_HOURS`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "window_hours": 24,
    "event_types": ["Log", "Metric", "Span", "Transaction"],
    "note": null
  },
  "meta": {
    "event_type_count": 4
  }
}
```

Use this first when you do not know which event type contains the data.

### `nr_describe_log_fields`

Discovers attribute names for a New Relic event type using `keyset()`.

Inputs:

```json
{
  "event_type": "Log",
  "hours": 1
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `event_type` | `string` | No | `Log` | Event type name, for example `Log`, `Transaction`, or `Span`. |
| `hours` | `integer` | No | `1` | Lookback window. Clamped by `NR_MAX_WINDOW_HOURS`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "event_type": "Log",
    "window_hours": 1,
    "fields": ["message", "timestamp", "trace.id", "order_id"],
    "correlation_id_candidates": ["trace.id", "order_id"],
    "note": null
  },
  "meta": {
    "field_count": 4
  }
}
```

Use this before writing NRQL filters. New Relic field names depend on the
ingestion pipeline.

### `nr_run_nrql_query`

Runs a read-only NRQL query.

Inputs:

```json
{
  "query": "SELECT timestamp, message FROM Log WHERE order_id = '88213' SINCE 1 day ago",
  "limit": 100
}
```

Input fields:

| Field | Type | Required | Default | Description |
|---|---:|---:|---:|---|
| `query` | `string` | Yes | N/A | NRQL query. Must be a single `SELECT`. |
| `limit` | `integer` | No | `100` | Requested row limit. Clamped by `NR_MAX_ROWS`. |

Success output:

```json
{
  "ok": true,
  "data": {
    "executed_query": "SELECT timestamp, message FROM Log WHERE order_id = '88213' SINCE 1 day ago LIMIT 100",
    "rows": [
      {
        "timestamp": 1786538460000,
        "message": "payment failed"
      }
    ],
    "row_count": 1,
    "metadata": {
      "eventTypes": ["Log"],
      "facets": []
    }
  },
  "meta": {
    "row_count": 1
  }
}
```

Guardrails:

- Query must start with `SELECT`.
- Multiple statements are rejected.
- Write-like keywords are rejected.
- `LIMIT` is injected or clamped.

## MCP Resources

Resources are read-only context. They are not tools.

| Resource | Description |
|---|---|
| `schema://db/tables` | Compact JSON list of database tables. |
| `schema://db/table/{name}` | Detailed schema for one table. |
| `logs://groups` | CloudWatch log group inventory. |
| `docs://query-cookbook` | Markdown query cookbook generated from `src/data/query_cookbook.yaml`. |

## How To Update A Tool

Follow this flow when changing or adding a tool.

1. Add or update the MCP tool wrapper in `src/tools/`.
2. Put external service logic in `src/integrations/<service>/`.
3. Put safety checks in `src/integrations/<service>/guardrail.py`.
4. Return the common response shape: `{"ok": true, "data": ..., "meta": ...}`.
5. Return structured errors by catching `ToolError` and calling `to_response()`.
6. Redact sensitive output before returning rows or log messages.
7. Add unit tests for guardrails and response behavior.
8. Run `uv run pytest tests/unit`.

Rules to keep:

- Never perform writes from an MCP tool in this project.
- Never return secrets, tokens, passwords, raw emails, phone numbers, card numbers, PAN, or Aadhaar values.
- Keep guardrail errors specific and self-correctable.
- Keep query limits server-controlled.
- Do not trust model-generated SQL, Logs Insights, or NRQL without validation.

## Where To Add Tests

| Change Type | Test Location |
|---|---|
| DB SQL validation | `tests/unit/test_db_guardrail.py` |
| DB introspection helper logic | `tests/unit/test_db_introspect.py` |
| CloudWatch validation | `tests/unit/test_cw_guardrail.py` |
| CloudWatch tool behavior | `tests/unit/test_cloudwatch_tools.py` |
| New Relic validation | `tests/unit/test_newrelic_guardrail.py` |
| New Relic tool behavior | `tests/unit/test_newrelic_tools.py` |
| Redaction behavior | `tests/unit/test_redaction.py` |
| Real Postgres behavior | `tests/integration/test_db_tools.py` |

## Quick Investigation Flow

For a typical support ticket:

1. Use `db_list_tables` to discover relevant tables.
2. Use `db_describe_table` on likely tables.
3. Use `db_search_by_identifier` or `db_resolve_store` to find key IDs.
4. Use `db_run_query` for focused database evidence.
5. Use `cw_list_log_groups` to find allowed log groups.
6. Use `cw_describe_log_fields` to find real log fields.
7. Use `cw_get_trace_events` or `cw_run_insights_query` for logs.
8. Use `nr_list_event_types`, `nr_describe_log_fields`, and `nr_run_nrql_query` if New Relic has the relevant telemetry.
9. Summarize findings with executed queries, timestamps, row counts, and evidence.
