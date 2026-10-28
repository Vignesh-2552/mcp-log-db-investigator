---
name: support-ticket-investigation
description: Investigate customer support tickets across Postgres, AWS CloudWatch Logs and New Relic using a read-only Investigation MCP server. Teaches the discover-then-query workflow (list, describe, then query), cross-source correlation by request/order id, and how to read the server's guardrail errors. Requires the open-source opsinsight-mcp server (installed automatically with `uvx opsinsight-mcp`) configured with your own credentials.
---

# Support Ticket Investigation

Use this skill when a user reports a support ticket ("checkout failed around 3pm", "order 88213 never confirmed")
and an **Investigation MCP server** is connected to the agent. The server exposes read-only tools for three
sources: PostgreSQL (`db_*`), CloudWatch Logs (`cw_*`) and New Relic NRQL (`nr_*`).

This skill does not contain the server. The user connects it with their own credentials: the server is the
`opsinsight-mcp` package on PyPI, and the MCP client launches it with `uvx opsinsight-mcp`. If the `db_*`, `cw_*`
or `nr_*` tools are not available, stop and point the user to `reference/setup.md`; the usual fix is adding
`scripts/mcp-config.example.json` to their client config and restarting the client. If only one source's tools
fail with a configuration error, that source's variables are missing; the other sources still work.

## Core rule: discover before you query

Guessing table, column, log field or attribute names is the main cause of queries that silently return zero rows.
For each source, always run the inventory tool, then the describe tool, then the query tool.

| Source | 1. Inventory | 2. Ground | 3. Query |
|---|---|---|---|
| Postgres | `db_list_tables` | `db_describe_table`, `db_sample_rows` | `db_run_query` (`db_explain_query` first if it may be expensive) |
| CloudWatch | `cw_list_log_groups` | `cw_describe_log_fields` | `cw_run_insights_query`, `cw_filter_events`, `cw_get_trace_events`, `cw_get_metric_stats` |
| New Relic | `nr_list_event_types` | `nr_describe_log_fields` | `nr_run_nrql_query` |

See `reference/tools.md` for each tool's purpose and parameters.

## Workflow

1. **Pin down the facts.** Extract identifiers (user id, order id, request id, store name or domain) and a time
   window from the ticket. Ask the user if neither is present. Keep the window as tight as possible.
2. **Resolve identifiers.** Use `db_resolve_store` for a store name or domain, and `db_search_by_identifier` for
   an id such as `order_id` or `user_id`. If the result says `ambiguous: true`, show the candidates and ask. Never
   pick one silently. Heed `data_freshness_note`: a "no matches" result may only cover historical schemas.
3. **Check the database.** Find the row(s) and status. Note any correlation field (`request_id`, `trace_id`).
4. **Trace in the logs.** Call `cw_describe_log_fields` or `nr_describe_log_fields` and read
   `correlation_id_candidates`. Then call `cw_get_trace_events` (CloudWatch) or `nr_run_nrql_query` (New Relic)
   with that field and the id, over the ticket's time window.
5. **Look for the pattern.** Use error-rate-over-time and top-error-message queries (see
   `reference/query-cookbook.md`) to tell one dominant failure from many unrelated ones. Use `cw_get_metric_stats`
   to check for an infra spike in the same window.
6. **Report.** Summarise what happened, in order, with timestamps and the evidence from each source. State what
   you could not verify. Do not claim a root cause the data does not show.

Cross-source correlation is manual. No tool joins the three sources for you, so merge results yourself by id and
timestamp (watch for timezone differences between sources).

## Response and error shape

Every tool returns `{"ok": true, "data": {...}, "meta": {...}}` or
`{"ok": false, "error": {"rule": ..., "message": ..., "detail": ..., "allowed": [...]}}`.

When `ok` is false, read `detail` and `allowed`, fix the query and retry. Do not give up after one rejection.
Typical causes: a non-`SELECT` statement, a blocked function, a log group outside the allowlist, a time window
over the cap, or a CloudWatch scan over the bytes-scanned ceiling (narrow the window or add filters).

## Safety behaviour you should expect and respect

- Everything is read-only. Only a single `SELECT` or `WITH ... SELECT` is accepted. LIMIT is injected or clamped.
- PII (emails, card numbers, Aadhaar, PAN, phone numbers and configured column names) is masked in rows and log
  text. Do not try to bypass redaction, and do not ask the user to disable it.
- CloudWatch is billed per GB scanned. Check `bytesScanned` in `meta`, keep windows short, and prefer
  `cw_filter_events` for simple greps.
- Never ask the user to paste credentials into the chat. Credentials live in the server's environment only.

## Reference

- `reference/setup.md`: run the server and connect your client
- `reference/tools.md`: all tools
- `reference/query-cookbook.md`: example queries per source
