from core.app import mcp

_MANUAL_CORRELATION_STEPS = """\
There is no automatic cross-source correlation tool in this build — you must
fan out and merge results yourself:

1. Pull `schema://db/tables` and `docs://query-cookbook` for grounding before
   writing any SQL.
2. Use `db_search_by_identifier` and/or `db_run_query` to gather DB evidence.
   Always report the `executed_sql` and row counts you got back.
3. Pull `logs://groups` to see which log groups exist, then call
   `cw_describe_log_fields` on each relevant one before writing a Logs
   Insights query, so your `filter`/`fields` clauses match real field names.
   If New Relic is also in play, call `nr_describe_log_fields` first too —
   New Relic Log attributes depend entirely on the ingestion pipeline (e.g.
   `trace.id`/`span.id` from logs-in-context vs. a custom `request_id`), so
   never guess a WHERE clause; use the `correlation_id_candidates` it
   returns to find the right identifier to join on.
4. Call `cw_run_insights_query` (or `cw_filter_events` for a simple grep)
   against every log group that could plausibly be involved, and/or
   `nr_run_nrql_query` for New Relic — do not assume a single service or a
   single log source. If a CloudWatch query returns `status: "Running"`,
   poll it again with the same `query_id` rather than giving up.
5. Merge the DB rows and log events yourself, sorted by timestamp, to build
   a single incident timeline. Note where identifiers match up
   (order_id/user_id/request_id) and where they don't.
6. Your final answer must cite specific row counts, timestamps, and the
   exact queries you ran — never state a conclusion you can't trace back to
   a tool result.\
"""


@mcp.prompt()
def investigate_incident(ticket_id: str, description: str, time_window: str) -> str:
    """Investigate a support ticket end-to-end: DB + log evidence, merged into an RCA."""
    return f"""\
Investigate support ticket {ticket_id}: "{description}"

Time window to focus on: {time_window}

Goal: produce a root-cause summary an engineer can paste into a reply to the
client, backed by evidence from the database and logs.

{_MANUAL_CORRELATION_STEPS}
"""


@mcp.prompt()
def trace_user_journey(user_id: str, date: str) -> str:
    """Reconstruct everything a specific user did/experienced on a given date."""
    return f"""\
Reconstruct user {user_id}'s journey on {date}: every order, payment attempt,
and related audit event, plus what the logs show for each of those events.

Goal: a single chronological timeline covering that user's activity and
anything that went wrong.

{_MANUAL_CORRELATION_STEPS}
"""


@mcp.prompt()
def slow_endpoint_rca(endpoint: str, time_window: str) -> str:
    """Root-cause a latency/slowness report for a specific endpoint or service."""
    return f"""\
Root-cause slowness reported for "{endpoint}" during {time_window}.

Goal: determine whether this is a DB-side issue (slow queries, lock
contention), an upstream/provider issue (e.g. payment gateway latency), or
an infra issue (CPU/memory/connection pool), and back that conclusion with
evidence.

Additional steps beyond the general correlation flow below:
- Use `db_explain_query` on any suspect query before concluding it's a DB
  bottleneck.
- Use `cw_get_metric_stats` to pull relevant infra metrics (latency, CPU,
  error rate) for the same window and compare against the log-derived
  latency percentiles (see the cookbook's "p95 latency" example).

{_MANUAL_CORRELATION_STEPS}
"""
