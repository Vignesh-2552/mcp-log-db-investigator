"""Time-window parsing and capping shared by every time-bounded source."""

from datetime import UTC, datetime, timedelta

from opsinsight.core.errors import ToolError


def parse_iso8601(value: str, error_cls: type[ToolError] = ToolError) -> datetime:
    """Parses an ISO 8601 timestamp; a value with no timezone is taken as UTC."""
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as e:
        raise error_cls(
            rule="invalid_timestamp", message=f"Could not parse timestamp: {value!r}.", detail=str(e)
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def clamp_window(
    start: str | None,
    end: str | None,
    default_hours: int,
    max_hours: int,
    error_cls: type[ToolError] = ToolError,
) -> tuple[datetime, datetime]:
    """Parses/defaults the [start, end] window and enforces the hard cap.
    `end` defaults to now and `start` to `default_hours` before `end`.
    `error_cls` lets a source raise its own `ToolError` subclass."""
    end_dt = parse_iso8601(end, error_cls) if end else datetime.now(UTC)
    start_dt = parse_iso8601(start, error_cls) if start else end_dt - timedelta(hours=default_hours)

    if start_dt >= end_dt:
        raise error_cls(rule="invalid_window", message="start must be before end.")

    span_hours = (end_dt - start_dt).total_seconds() / 3600
    if span_hours > max_hours:
        raise error_cls(
            rule="window_too_large",
            message=f"Requested window ({span_hours:.1f}h) exceeds the max of {max_hours}h.",
            detail=f"Narrow the time window to at most {max_hours} hours.",
        )
    return start_dt, end_dt
