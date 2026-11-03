from opsinsight.core.app import mcp
from opsinsight.prompts.common import INVESTIGATION_RULES, quote_input


@mcp.prompt()
def trace_user_journey(
    identifier: str,
    id_type: str = "auto",
    time_window: str = "24h",
) -> str:
    """Follow one identifier (order id, user id, request id) across the database,
    CloudWatch logs and New Relic, and lay out its journey as one timeline.
    `id_type` is the column/field the identifier belongs to, or "auto"."""
    id_type = id_type.strip() or "auto"
    if id_type == "auto":
        db_hint = (
            "Work out which column holds this identifier (check its shape, then confirm "
            "with `db_search_by_identifier` or `db_describe_table`) and pass that column "
            "as `db_id_type`."
        )
    else:
        db_hint = (
            f"The identifier type is `{id_type}`: pass `db_id_type=\"{id_type}\"` if a "
            "database column has that name, and try it as `cw_field` / `nr_field` after "
            "confirming it exists with the describe tools."
        )
    return f"""\
# Trace a user journey

You are reconstructing everything that happened to one identifier, across every
source, with the OpsInsight read-only tools.

{quote_input("Identifier", identifier)}

Identifier type: `{id_type}`
Time window: `{time_window.strip()}`

{INVESTIGATION_RULES}
## Steps

1. **Fan out with `trace_request`.** Call it with the identifier as `value` and the UTC
   `start`/`end`. It searches CloudWatch and New Relic for the identifier, but it only
   searches the database when `db_id_type` is given. {db_hint}
2. **Check each source's status.** Read `data.sources`. A source with status `ok` and
   zero events usually means the field name is wrong for that source, not that the
   request never reached it: find the right field with `cw_describe_log_fields` /
   `nr_describe_log_fields` (`correlation_id_candidates`) and call `trace_request`
   again with `cw_field` / `nr_field`. A source with an error is reported there too;
   say so in the output rather than treating it as "no activity".
3. **Rebuild the timeline.** `trace_request` returns events sorted oldest first, each
   tagged with `source`. Database rows are **not** filtered by the time window, so
   check each row's `in_window` flag. The database shows current row state, not its
   change history: only describe state changes if an audit or history table shows them
   (the cookbook's audit-trail query is a starting point).
4. **Find the anomaly.** Look for errors, unexpected status values, gaps where an
   expected step is missing, retries of the same action, and timeouts. Use
   `cw_get_trace_events` or `nr_run_nrql_query` to pull more detail around the
   suspicious events.
5. **Output** a journey table with columns
   `Timestamp (UTC) | Source | Service / table | Action or state | Status | Details`,
   followed by a short list of anomalies and what each one implies. State which sources
   returned nothing and why, if known.
"""
