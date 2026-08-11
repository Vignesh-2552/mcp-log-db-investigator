import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone

from investigation_server.errors import CWGuardrailError

# Poll with backoff, capped at the caller's max_wait_s.
_BACKOFF_SCHEDULE: tuple[float, ...] = (0.5, 1, 2, 4, 8, 8, 8, 8, 8, 8, 8, 8)

_RUNNING_STATUSES = {"Running", "Scheduled"}


def validate_log_groups(requested: list[str], allowlist: frozenset[str]) -> list[str]:
    if not requested:
        raise CWGuardrailError(rule="no_log_groups", message="At least one log group must be specified.")
    disallowed = [g for g in requested if g not in allowlist]
    if disallowed:
        raise CWGuardrailError(
            rule="log_group_not_allowed",
            message=f"Log group(s) not allowed: {', '.join(disallowed)}.",
            allowed=sorted(allowlist),
        )
    return requested


def _parse_iso8601(value: str) -> datetime:
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise CWGuardrailError(
            rule="invalid_timestamp", message=f"Could not parse timestamp: {value!r}.", detail=str(e)
        ) from e
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def clamp_window(
    start: str | None,
    end: str | None,
    default_hours: int,
    max_hours: int,
) -> tuple[datetime, datetime]:
    """Parses/defaults the [start, end] window and enforces the hard cap
    (default 24h, hard cap 7 days)."""
    now = datetime.now(timezone.utc)
    end_dt = _parse_iso8601(end) if end else now
    start_dt = _parse_iso8601(start) if start else end_dt - timedelta(hours=default_hours)

    if start_dt >= end_dt:
        raise CWGuardrailError(rule="invalid_window", message="start must be before end.")

    span_hours = (end_dt - start_dt).total_seconds() / 3600
    if span_hours > max_hours:
        raise CWGuardrailError(
            rule="window_too_large",
            message=f"Requested window ({span_hours:.1f}h) exceeds the max of {max_hours}h.",
            detail=f"Narrow the time window to at most {max_hours} hours.",
        )
    return start_dt, end_dt


def check_bytes_scanned(bytes_scanned: int, ceiling: int) -> None:
    if bytes_scanned > ceiling:
        raise CWGuardrailError(
            rule="bytes_scanned_ceiling_exceeded",
            message=f"Query scanned {bytes_scanned} bytes, exceeding the {ceiling} byte ceiling.",
            detail="Narrow the time window or add a more selective filter and retry.",
        )


def poll_query_with_backoff(
    poll_fn: Callable[[], dict],
    max_wait_s: int,
    schedule: tuple[float, ...] = _BACKOFF_SCHEDULE,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """Calls poll_fn() repeatedly with backoff until a terminal status is
    reported or max_wait_s elapses."""
    elapsed = 0.0
    result = poll_fn()
    for delay in schedule:
        if result.get("status") not in _RUNNING_STATUSES:
            return result
        if elapsed >= max_wait_s:
            break
        actual_delay = min(delay, max_wait_s - elapsed)
        if actual_delay > 0:
            sleep_fn(actual_delay)
            elapsed += actual_delay
        result = poll_fn()
    return result
