import httpx

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from integrations.newrelic.guardrail import validate_nrql
from tools.newrelic import utils


@mcp.tool()
async def nr_describe_log_fields(event_type: str = "Log", hours: int = 1) -> dict:
    """Discover the real attribute names present on a New Relic event type
    (default: Log) over a recent window, via NRQL's `keyset()`. Run this
    before writing a WHERE clause — New Relic Log attributes are whatever
    your ingestion pipeline set (e.g. `trace.id`/`span.id` from
    logs-in-context, or a custom `request_id`/`order_id`), and guessing
    field names is the main reason generated NRQL comes back empty.
    Highlights likely trace/correlation identifiers separately."""
    settings = get_settings()
    if not utils.EVENT_TYPE_RE.match(event_type):
        return ToolError(
            rule="invalid_event_type",
            message="event_type must be a bare identifier (letters, digits, underscore).",
            detail=f"Got: {event_type!r}",
        ).to_response()

    window = max(1, min(hours, settings.nr_max_window_hours))
    query = f"SELECT keyset() FROM {event_type} SINCE {window} hour ago"
    try:
        validated_query = validate_nrql(query, settings.nr_max_rows)
        result = await utils.run_nrql(validated_query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return utils.httpx_error_response(e)

    rows = result["results"]
    keys = sorted(rows[0].get("keyset", [])) if rows and isinstance(rows[0], dict) else []
    correlation_candidates = sorted(k for k in keys if utils.CORRELATION_ID_RE.search(k))
    note = None
    if not keys:
        note = (
            f"No {event_type} events found in the last {window}h — widen `hours` or "
            "confirm the event type name (e.g. Log, Transaction, Span)."
        )

    return {
        "ok": True,
        "data": {
            "event_type": event_type,
            "window_hours": window,
            "fields": keys,
            "correlation_id_candidates": correlation_candidates,
            "note": note,
        },
        "meta": {"field_count": len(keys)},
    }
