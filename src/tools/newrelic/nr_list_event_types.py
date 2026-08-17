import httpx

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from tools.newrelic import utils


@mcp.tool()
async def nr_list_event_types(hours: int = 24) -> dict:
    """List event types with data in the account over a recent window, via NRQL's
    `SHOW EVENT TYPES`. Run this first when you don't already know which event
    type to target — e.g. before calling `nr_describe_log_fields` or
    `nr_run_nrql_query` against `Log`, `Transaction`, `Span`, `Metric`, or a
    custom event type your ingestion pipeline defines."""
    settings = get_settings()
    window = max(1, min(hours, settings.nr_max_window_hours))
    query = f"SHOW EVENT TYPES SINCE {window} hour ago"
    try:
        result = await utils.run_nrql(query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return utils.httpx_error_response(e)

    rows = result["results"]
    event_types = sorted(
        {v for row in rows if isinstance(row, dict) for v in row.values() if isinstance(v, str)}
    )
    note = None
    if not event_types:
        note = f"No event types found with data in the last {window}h — widen `hours`."

    return {
        "ok": True,
        "data": {"window_hours": window, "event_types": event_types, "note": note},
        "meta": {"event_type_count": len(event_types)},
    }
