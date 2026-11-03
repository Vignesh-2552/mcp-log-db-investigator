from opsinsight.core.app import mcp
from opsinsight.prompts.common import INVESTIGATION_RULES, quote_input


@mcp.prompt()
def slow_endpoint_rca(
    endpoint_or_transaction: str,
    time_window: str = "4h",
    threshold_ms: int = 1000,
) -> str:
    """Diagnose high latency or timeouts for an HTTP route or New Relic transaction
    name, down to the slow database queries and infrastructure saturation behind it.
    `threshold_ms` is the latency SLA used to count slow requests."""
    threshold_s = threshold_ms / 1000
    return f"""\
# Root-cause a slow endpoint

You are diagnosing high latency for one endpoint with the OpsInsight read-only tools.

{quote_input("Endpoint or transaction", endpoint_or_transaction)}

Time window: `{time_window.strip()}`
Latency threshold: {threshold_ms} ms (New Relic `duration` is in seconds, so use
`duration > {threshold_s:g}`).

{INVESTIGATION_RULES}
## Steps

1. **Find the transaction.** Confirm the `Transaction` event type exists with
   `nr_list_event_types`. If you were given a URL path rather than a transaction name,
   find the matching `name` with a `FACET name` query filtered on `request.uri`, then
   use that exact name in later queries.
2. **Break down the latency** with `nr_run_nrql_query` (see the cookbook's
   `Transaction` queries): `percentile(duration, 50, 95, 99)` over time, the share of
   requests over the threshold, the error rate, and `average(databaseDuration)` /
   `average(externalDuration)` against `average(duration)`. This tells you whether the
   time goes to the database, an external call, or the application itself. Widen the
   window once to compare with a normal period before concluding anything is "slow".
3. **If the database dominates**, find the slow statements. New Relic `Span` events
   with `category = 'datastore'` carry `db.statement` when query recording is enabled;
   facet on it to rank statements by duration. For a statement that matches a
   `SELECT` you can reproduce, run `db_explain_query` and look for sequential scans on
   large tables, missing indexes on `WHERE`/`JOIN` columns and large row estimates.
   Use `db_describe_table` to check which indexes exist. Never run a write or DDL to
   test a fix.
4. **If external calls dominate**, facet external spans (`category = 'http'`) by host or
   name to find the slow dependency.
5. **Check infrastructure saturation** with `cw_get_metric_stats` (e.g. `AWS/RDS`
   `CPUUtilization`, `DatabaseConnections`, `FreeableMemory`; ECS or Lambda
   throttling). These need the exact dimension values, such as `DBInstanceIdentifier`:
   ask the user for them rather than guessing. Line the metric spikes up with the
   latency spikes from step 2.
6. **Report**: the latency numbers (p50/p95/p99, share over {threshold_ms} ms, error
   rate) for the slow period versus normal, where the time goes, the evidence for the
   bottleneck, and concrete remedies (e.g. a specific missing index, a slow external
   dependency, connection pool exhaustion). Mark anything not backed by a query result
   as a hypothesis.
"""
