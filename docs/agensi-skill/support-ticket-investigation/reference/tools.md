# Tool reference

All tools are read-only and return `{ok, data, meta}` or `{ok: false, error}`.

## PostgreSQL

| Tool | Purpose |
|---|---|
| `db_list_tables` | Tables with row estimates and comments (optional `schema`). Cached about 10 minutes. |
| `db_describe_table` | Columns, types, nullability, PK/FK and indexes. The main grounding tool. |
| `db_sample_rows` | PII-masked sample rows, to learn value formats such as status enums. |
| `db_explain_query` | `EXPLAIN (FORMAT JSON)` on a validated SELECT. Run before expensive queries. |
| `db_run_query` | Read-only SELECT. Returns rows, columns, row count and elapsed ms. |
| `db_search_by_identifier` | Find rows by an identifier. `id_type` is any column-name-like string such as `order_id`. Tags results `live` or `historical`. |
| `db_resolve_store` | Resolve a store name or domain to a `store_id`. Check `ambiguous` before trusting one id. |

## CloudWatch Logs

| Tool | Purpose |
|---|---|
| `cw_list_log_groups` | Allowlisted log groups with retention and stored bytes. |
| `cw_describe_log_fields` | Sampled log shapes and field frequencies, with `correlation_id_candidates`. |
| `cw_run_insights_query` | Logs Insights query. `limit` is the authoritative row cap. Aborts over the bytes-scanned ceiling. |
| `cw_get_trace_events` | Every line in a log group where `field` equals `value`, in time order. |
| `cw_filter_events` | Cheap pattern grep over the last N minutes. |
| `cw_get_metric_stats` | Metric datapoints (avg/sum/min/max/sample count) to correlate with infra load. |

## New Relic

| Tool | Purpose |
|---|---|
| `nr_list_event_types` | Event types with data in the account (`SHOW EVENT TYPES`). |
| `nr_describe_log_fields` | Real attribute names via `keyset()`, with likely correlation ids flagged. |
| `nr_run_nrql_query` | Read-only NRQL. SELECT only, LIMIT enforced, window capped. |
