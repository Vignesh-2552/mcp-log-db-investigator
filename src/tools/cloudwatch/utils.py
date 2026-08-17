import re
from datetime import datetime
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from core.errors import ToolError
from core.logging_config import get_logger
from core.redaction import redact_log_event
from integrations.cloudwatch.client import get_logs_client, get_metrics_client
from integrations.cloudwatch.guardrail import (
    check_bytes_scanned,
    clamp_window,
    poll_query_with_backoff,
    validate_log_groups,
)

__all__ = [
    "BYTES_SCANNED_SAFETY_MARGIN",
    "CORRELATION_ID_RE",
    "ERROR_BOOST_FILTER_PATTERN",
    "FILTER_EVENTS_CAP",
    "BotoCoreError",
    "ClientError",
    "aws_error_response",
    "build_rows",
    "clamp_window",
    "epoch_millis",
    "epoch_seconds",
    "flatten_field_paths",
    "get_logs_client",
    "get_metrics_client",
    "logger",
    "run_insights_query",
    "suggest_window_seconds",
    "validate_log_groups",
]

logger = get_logger("cloudwatch.tools")

FILTER_EVENTS_CAP = 1000
ERROR_BOOST_FILTER_PATTERN = "?ERROR ?error ?Error ?FATAL ?fatal ?CRITICAL ?critical"
CORRELATION_ID_RE = re.compile(
    r"(trace|span|request|correlation|order|user|session|transaction)[._-]?id", re.IGNORECASE
)
BYTES_SCANNED_SAFETY_MARGIN = 0.8  # target 80% of the ceiling, not the ceiling itself


def epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def epoch_millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def aws_error_response(e: Exception) -> dict:
    logger.error("AWS CloudWatch API error: %s", e)
    return ToolError(rule="aws_error", message="AWS CloudWatch call failed.", detail=str(e)).to_response()


def flatten_field_paths(obj: Any, prefix: str = "", max_depth: int = 4) -> set[str]:
    """Dotted/bracketed field-path enumeration, e.g. message.user.storeId,
    message.errors[].path. Depth-capped to avoid blowup on deeply nested
    payloads; only enumerates names, never values."""
    paths: set[str] = set()
    if max_depth <= 0:
        return paths
    if isinstance(obj, dict):
        for key, value in obj.items():
            path = f"{prefix}.{key}" if prefix else key
            paths.add(path)
            paths |= flatten_field_paths(value, path, max_depth - 1)
    elif isinstance(obj, list) and obj:
        paths |= flatten_field_paths(obj[0], f"{prefix}[]", max_depth - 1)
    return paths


def build_rows(results: list[list[dict]], settings, include_ptr: bool) -> list[dict]:
    return [
        {
            field["field"]: redact_log_event(field["value"], settings)
            for field in row
            if include_ptr or field["field"] != "@ptr"
        }
        for row in results
    ]


def suggest_window_seconds(
    bytes_scanned: int, ceiling: int, start_dt: datetime, end_dt: datetime
) -> int | None:
    """Back-solves a window size that would land under the ceiling, from this
    call's observed scan rate — so a caller can retry correctly on the first
    try instead of manually bisecting the time window."""
    requested_window_s = (end_dt - start_dt).total_seconds()
    if requested_window_s <= 0 or bytes_scanned <= 0:
        return None
    scan_rate = bytes_scanned / requested_window_s
    return max(int((ceiling * BYTES_SCANNED_SAFETY_MARGIN) / scan_rate), 1)


def run_insights_query(
    settings,
    log_groups: list[str],
    query: str,
    start: str,
    end: str,
    limit: int,
    include_ptr: bool,
) -> dict:
    query_id: str | None = None
    bytes_scanned: int | None = None
    start_dt: datetime | None = None
    end_dt: datetime | None = None
    try:
        validate_log_groups(log_groups, settings.cw_log_group_allowlist_set)
        start_dt, end_dt = clamp_window(
            start, end, settings.cw_default_window_hours, settings.cw_max_window_hours
        )
        client = get_logs_client(settings)
        started = client.start_query(
            logGroupNames=log_groups,
            startTime=epoch_seconds(start_dt),
            endTime=epoch_seconds(end_dt),
            queryString=query,
            limit=limit,
        )
        query_id = started["queryId"]

        def poll_fn() -> dict:
            resp = client.get_query_results(queryId=query_id)
            stats = resp.get("statistics", {})
            return {
                "status": resp.get("status"),
                "results": resp.get("results", []),
                "bytes_scanned": int(stats.get("bytesScanned", 0)),
                "records_matched": stats.get("recordsMatched"),
            }

        poll_result = poll_query_with_backoff(poll_fn, settings.cw_poll_max_wait_s)
        bytes_scanned = poll_result.get("bytes_scanned", 0)

        if bytes_scanned > settings.cw_max_bytes_scanned:
            if poll_result.get("status") in ("Running", "Scheduled"):
                client.stop_query(queryId=query_id)
            check_bytes_scanned(bytes_scanned, settings.cw_max_bytes_scanned)

    except ToolError as e:
        response = e.to_response()
        meta = {"bytes_scanned": bytes_scanned, "query_id": query_id}
        if e.rule == "bytes_scanned_ceiling_exceeded" and start_dt and end_dt:
            suggestion = suggest_window_seconds(
                bytes_scanned, settings.cw_max_bytes_scanned, start_dt, end_dt
            )
            if suggestion is not None:
                meta["suggested_max_window_seconds"] = suggestion
                response["error"]["detail"] = (
                    f"{response['error']['detail']} Based on this query's observed scan "
                    f"rate, try narrowing the window to about {suggestion}s "
                    f"(~{suggestion / 3600:.2f}h) or add a more selective filter."
                )
        response["meta"] = meta
        return response
    except (BotoCoreError, ClientError) as e:
        return aws_error_response(e)

    if poll_result["status"] in ("Running", "Scheduled"):
        return {
            "ok": True,
            "data": {
                "status": "Running",
                "query_id": query_id,
                "note": "Query is still running; poll again later with this query_id.",
                "bytes_scanned": bytes_scanned,
            },
            "meta": {"bytes_scanned": bytes_scanned},
        }

    rows = build_rows(poll_result["results"], settings, include_ptr)
    return {
        "ok": True,
        "data": {
            "status": poll_result["status"],
            "query_id": query_id,
            "rows": rows,
            "row_count": len(rows),
            "bytes_scanned": bytes_scanned,
            "records_matched": poll_result.get("records_matched"),
            "ptr_included": include_ptr,
        },
        "meta": {"row_count": len(rows), "bytes_scanned": bytes_scanned},
    }
