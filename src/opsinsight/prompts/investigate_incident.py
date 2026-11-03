from opsinsight.core.app import mcp
from opsinsight.prompts.common import INVESTIGATION_RULES, quote_input


@mcp.prompt()
def investigate_incident(
    ticket_text: str,
    time_window: str = "24h",
    store_or_domain: str | None = None,
) -> str:
    """Guided root-cause investigation of a customer support ticket across
    PostgreSQL, CloudWatch and New Relic. `time_window` is a relative span such as
    "2h" or "24h", or an ISO 8601 interval "start/end"."""
    store_step = (
        f"   - The ticket names a store or domain: call `db_resolve_store` with "
        f"`{store_or_domain.strip()}` first. If the result says `ambiguous: true`, show the "
        "candidates to the user and ask which one; never pick one silently.\n"
        if store_or_domain and store_or_domain.strip()
        else "   - If the ticket names a store or domain, resolve it with `db_resolve_store`. "
        "If the result says `ambiguous: true`, ask the user which candidate is meant.\n"
    )
    return f"""\
# Investigate a support ticket

You are investigating a customer support ticket with the OpsInsight read-only tools.
Work through the steps below in order and finish with the report described in step 6.

{quote_input("Ticket", ticket_text)}

Time window: `{time_window.strip()}`

{INVESTIGATION_RULES}
## Steps

1. **Pin down the facts.** List every identifier in the ticket (order id, user or
   customer id, request id, error code, store) and turn the time window into absolute
   UTC `start`/`end`. If the ticket has no identifier and no usable time, ask the user
   before querying anything.
2. **Resolve identifiers in the database.**
{store_step}\
   - For each id, call `db_search_by_identifier` with the id and the column name it
     belongs to (e.g. `order_id`, `customer_id`). Read `source_type` and
     `data_freshness_note`: a "no matches" result may only cover historical tables.
3. **Ground the database rows.** Call `db_describe_table` on the tables that matched,
   then use `db_run_query` to read the current state (status, timestamps, error
   columns). Note any correlation column such as `request_id` or `trace_id`.
4. **Trace across sources.** Call `trace_request` with the best identifier, the UTC
   `start`/`end`, and `db_id_type` set to its column name so the database is searched
   too. Read `data.sources`: a source with status `ok` and zero events usually means
   the field name is wrong for that source. Find the right one with
   `cw_describe_log_fields` / `nr_describe_log_fields` (`correlation_id_candidates`)
   and pass it as `cw_field` / `nr_field`, or dig into one source with
   `cw_get_trace_events` or `nr_run_nrql_query`.
5. **Look for the pattern.** Use the cookbook's error-rate-over-time and
   top-error-message queries to tell one dominant failure from many unrelated ones.
   If the user can tell you the infrastructure involved (e.g. an RDS instance id),
   check `cw_get_metric_stats` for a spike in the same window; do not guess metric
   namespaces or dimensions.
6. **Report** in this structure:
   - **Summary**: one or two sentences on what happened to the customer.
   - **Timeline**: timestamped events, each tagged with its source.
   - **Verified facts** and, separately, **unverified hypotheses**.
   - **Root cause**: only what the evidence shows; say "not determined" otherwise.
   - **Recommended resolution**, and a short **customer-facing reply** draft that
     contains no internal identifiers or PII.
"""
