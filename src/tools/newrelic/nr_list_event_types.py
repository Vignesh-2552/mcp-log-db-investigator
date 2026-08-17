from core.app import mcp
from core.container import get_container


@mcp.tool()
async def nr_list_event_types(hours: int = 24) -> dict:
    """List event types with data in the account over a recent window, via NRQL's
    `SHOW EVENT TYPES`. Run this first when you don't already know which event
    type to target — e.g. before calling `nr_describe_log_fields` or
    `nr_run_nrql_query` against `Log`, `Transaction`, `Span`, `Metric`, or a
    custom event type your ingestion pipeline defines."""
    return await get_container().newrelic_service.list_event_types(hours)
