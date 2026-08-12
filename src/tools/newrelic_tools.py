import re

import httpx

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from core.redaction import redact_rows
from integrations.newrelic.client import run_nrql
from integrations.newrelic.guardrail import validate_nrql

_EVENT_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_CORRELATION_ID_RE = re.compile(
    r"(trace|span|request|correlation|order|user|session|transaction)[._-]?id", re.IGNORECASE
)


def _httpx_error_response(e: httpx.HTTPError) -> dict:
    if isinstance(e, httpx.HTTPStatusError):
        return ToolError(
            rule="newrelic_http_error",
            message=f"New Relic API returned HTTP {e.response.status_code}.",
            detail=e.response.text[:500],
        ).to_response()
    return ToolError(
        rule="newrelic_connection_error", message="Could not reach New Relic API.", detail=str(e)
    ).to_response()


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
    if not _EVENT_TYPE_RE.match(event_type):
        return ToolError(
            rule="invalid_event_type",
            message="event_type must be a bare identifier (letters, digits, underscore).",
            detail=f"Got: {event_type!r}",
        ).to_response()

    window = max(1, min(hours, settings.nr_max_window_hours))
    query = f"SELECT keyset() FROM {event_type} SINCE {window} hour ago"
    try:
        validated_query = validate_nrql(query, settings.nr_max_rows)
        result = await run_nrql(validated_query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return _httpx_error_response(e)

    rows = result["results"]
    keys = sorted(rows[0].get("keyset", [])) if rows and isinstance(rows[0], dict) else []
    correlation_candidates = sorted(k for k in keys if _CORRELATION_ID_RE.search(k))
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
        result = await run_nrql(query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return _httpx_error_response(e)

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


@mcp.tool()
async def nr_run_nrql_query(query: str, limit: int = 100) -> dict:
    """Run a read-only NRQL query against New Relic (Log/Metric/event data).
    Only SELECT queries are permitted; LIMIT is enforced/clamped server-side."""
    settings = get_settings()
    try:
        validated_query = validate_nrql(query, settings.nr_max_rows, requested_limit=limit)
        result = await run_nrql(validated_query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return _httpx_error_response(e)

    rows = result["results"]
    if rows and isinstance(rows[0], dict):
        rows = redact_rows(rows, settings)

    return {
        "ok": True,
        "data": {
            "executed_query": validated_query,
            "rows": rows,
            "row_count": len(rows),
            "metadata": result["metadata"],
        },
        "meta": {"row_count": len(rows)},
    }
