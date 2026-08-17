from core.app import mcp
from core.container import get_container


@mcp.tool()
async def nr_describe_log_fields(event_type: str = "Log", hours: int = 1) -> dict:
    """Discover the real attribute names present on a New Relic event type
    (default: Log) over a recent window, via NRQL's `keyset()`. Run this
    before writing a WHERE clause — New Relic Log attributes are whatever
    your ingestion pipeline set (e.g. `trace.id`/`span.id` from
    logs-in-context, or a custom `request_id`/`order_id`), and guessing
    field names is the main reason generated NRQL comes back empty.
    Highlights likely trace/correlation identifiers separately."""
    return await get_container().newrelic_service.describe_log_fields(event_type, hours)
