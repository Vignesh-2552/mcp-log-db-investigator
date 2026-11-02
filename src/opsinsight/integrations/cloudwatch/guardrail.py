import time
from collections.abc import Callable
from datetime import datetime

from opsinsight.core.errors import CWGuardrailError
from opsinsight.core.logging_config import get_logger
from opsinsight.core.time_window import clamp_window as _clamp_window
from opsinsight.integrations.cloudwatch.constants import (
    BACKOFF_SCHEDULE,
    MAX_INSIGHTS_QUERY_CHARS,
    RUNNING_STATUSES,
    SOURCE_COMMAND_RE,
    TRACE_FIELD_RE,
)

logger = get_logger("cloudwatch.guardrail")


def validate_log_groups(requested: list[str], allowlist: frozenset[str]) -> list[str]:
    if not requested:
        logger.warning("CloudWatch guardrail error: no log groups specified")
        raise CWGuardrailError(rule="no_log_groups", message="At least one log group must be specified.")
    disallowed = [g for g in requested if g not in allowlist]
    if disallowed:
        logger.warning("CloudWatch guardrail error (log_group_not_allowed): %s disallowed", disallowed)
        raise CWGuardrailError(
            rule="log_group_not_allowed",
            message=f"Log group(s) not allowed: {', '.join(disallowed)}.",
            allowed=sorted(allowlist),
        )
    logger.debug("CloudWatch log groups validated: %s", requested)
    return requested


def clamp_window(
    start: str | None,
    end: str | None,
    default_hours: int,
    max_hours: int,
) -> tuple[datetime, datetime]:
    """`core.time_window.clamp_window`, raising `CWGuardrailError`."""
    return _clamp_window(start, end, default_hours, max_hours, error_cls=CWGuardrailError)


def validate_insights_query(query: str) -> str:
    """Logs Insights' `SOURCE logGroups(...)` command selects log groups from
    inside the query text; reject it so the only log groups a query can touch
    are the allowlist-checked `log_groups` argument."""
    if len(query) > MAX_INSIGHTS_QUERY_CHARS:
        raise CWGuardrailError(
            rule="query_too_long",
            message=f"Query is {len(query)} characters; the maximum is {MAX_INSIGHTS_QUERY_CHARS}.",
        )
    if SOURCE_COMMAND_RE.search(query):
        logger.warning("CloudWatch guardrail error (source_command_not_allowed)")
        raise CWGuardrailError(
            rule="source_command_not_allowed",
            message="The SOURCE command is not allowed in Logs Insights queries.",
            detail="Pass log groups via the `log_groups` argument instead; they are checked against the allowlist.",
        )
    return query


def validate_trace_field(field: str) -> str:
    if not TRACE_FIELD_RE.match(field):
        logger.warning("CloudWatch guardrail error (invalid_field_name): %r rejected", field)
        raise CWGuardrailError(
            rule="invalid_field_name",
            message="field must look like a Logs Insights field name (letters, digits, "
            "underscore, dot; optionally starting with @).",
            detail=f"Got: {field!r}",
        )
    return field


def build_trace_filter_value(value: str) -> str:
    """Escapes `value` for safe embedding inside `filter <field> = "<value>"`.
    Logs Insights has no parameterized-query API and no statement stacking via
    `;`; the exploitable surface is breaking out of the string literal via an
    unescaped `"` or a newline that starts a new pipeline stage, so backslash-
    escape `\\`/`"` and reject embedded newlines outright."""
    if "\n" in value or "\r" in value:
        logger.warning("CloudWatch guardrail error (invalid_trace_value): newline in value")
        raise CWGuardrailError(
            rule="invalid_trace_value",
            message="value must not contain newlines.",
            detail=f"Got: {value!r}",
        )
    return value.replace("\\", "\\\\").replace('"', '\\"')


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
    schedule: tuple[float, ...] = BACKOFF_SCHEDULE,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict:
    """Calls poll_fn() repeatedly with backoff until a terminal status is
    reported or max_wait_s elapses."""
    elapsed = 0.0
    result = poll_fn()
    for delay in schedule:
        if result.get("status") not in RUNNING_STATUSES:
            return result
        if elapsed >= max_wait_s:
            break
        actual_delay = min(delay, max_wait_s - elapsed)
        if actual_delay > 0:
            sleep_fn(actual_delay)
            elapsed += actual_delay
        result = poll_fn()
    return result
