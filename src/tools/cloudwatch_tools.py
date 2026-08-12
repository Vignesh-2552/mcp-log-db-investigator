import hashlib
import json
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from core.logging_config import get_logger
from core.redaction import redact_log_event
from integrations.cloudwatch.client import (
    get_logs_client,
    get_metrics_client,
)
from integrations.cloudwatch.guardrail import (
    build_trace_filter_value,
    check_bytes_scanned,
    clamp_window,
    poll_query_with_backoff,
    validate_log_groups,
    validate_trace_field,
)

logger = get_logger("cloudwatch.tools")

_FILTER_EVENTS_CAP = 1000
_ERROR_BOOST_FILTER_PATTERN = "?ERROR ?error ?Error ?FATAL ?fatal ?CRITICAL ?critical"
_CORRELATION_ID_RE = re.compile(
    r"(trace|span|request|correlation|order|user|session|transaction)[._-]?id", re.IGNORECASE
)


def _epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def _epoch_millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _aws_error_response(e: Exception) -> dict:
    logger.error("AWS CloudWatch API error: %s", e)
    return ToolError(rule="aws_error", message="AWS CloudWatch call failed.", detail=str(e)).to_response()


def _flatten_field_paths(obj: Any, prefix: str = "", max_depth: int = 4) -> set[str]:
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
            paths |= _flatten_field_paths(value, path, max_depth - 1)
    elif isinstance(obj, list) and obj:
        paths |= _flatten_field_paths(obj[0], f"{prefix}[]", max_depth - 1)
    return paths


@mcp.tool()
def cw_list_log_groups(prefix: str | None = None) -> dict:
    """List log groups (name, retention, stored bytes), filtered to the allowlist."""
    settings = get_settings()
    allowlist = settings.cw_log_group_allowlist_set
    try:
        client = get_logs_client(settings)
        kwargs = {"logGroupNamePrefix": prefix} if prefix else {}
        groups = []
        paginator = client.get_paginator("describe_log_groups")
        for page in paginator.paginate(**kwargs):
            for g in page.get("logGroups", []):
                name = g.get("logGroupName", "")
                if allowlist and name not in allowlist:
                    continue
                groups.append(
                    {
                        "log_group": name,
                        "retention_days": g.get("retentionInDays"),
                        "stored_bytes": g.get("storedBytes"),
                    }
                )
    except (BotoCoreError, ClientError) as e:
        return _aws_error_response(e)
    return {"ok": True, "data": {"log_groups": groups}, "meta": {"row_count": len(groups)}}


@mcp.tool()
def cw_describe_log_fields(log_group: str) -> dict:
    """Sample recent events from a log group and report discovered JSON shapes
    (clustered by top-level key set, since one log group can hold structurally
    different event types — e.g. access logs vs. app-error logs) with each
    shape's own field-frequency table, to ground Logs Insights query
    generation. Boosts the sample with error/fatal-level events so rare-but-
    important shapes aren't drowned out by a purely random sample."""
    settings = get_settings()
    try:
        validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
        client = get_logs_client(settings)
        now = datetime.now(UTC)
        start_time = _epoch_millis(now - timedelta(hours=1))

        random_events = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            limit=settings.cw_describe_fields_sample_size,
        ).get("events", [])
        error_events = client.filter_log_events(
            logGroupName=log_group,
            startTime=start_time,
            filterPattern=_ERROR_BOOST_FILTER_PATTERN,
            limit=settings.cw_describe_fields_error_boost_size,
        ).get("events", [])

        seen_ids = {e.get("eventId") for e in random_events}
        tagged_events = [(e, "random") for e in random_events]
        for e in error_events:
            event_id = e.get("eventId")
            if event_id not in seen_ids:
                seen_ids.add(event_id)
                tagged_events.append((e, "error_boosted"))

        clusters: dict[frozenset, dict[str, Any]] = {}
        parsed = 0
        for event, origin in tagged_events:
            try:
                obj = json.loads(event.get("message", ""))
            except (json.JSONDecodeError, TypeError):
                continue
            if not isinstance(obj, dict):
                continue
            parsed += 1
            sig = frozenset(obj.keys())
            cluster = clusters.setdefault(
                sig,
                {
                    "top_level_keys": sorted(sig),
                    "row_count": 0,
                    "composition": {"random": 0, "error_boosted": 0},
                    "field_counts": defaultdict(int),
                    "example_event": event.get("message", ""),
                },
            )
            cluster["row_count"] += 1
            cluster["composition"][origin] += 1
            for path in _flatten_field_paths(obj):
                cluster["field_counts"][path] += 1

        shapes = []
        for _, cluster in sorted(clusters.items(), key=lambda kv: -kv[1]["row_count"]):
            shape_id = hashlib.sha1(",".join(cluster["top_level_keys"]).encode()).hexdigest()[:8]
            fields = [
                {"field": k, "frequency": v}
                for k, v in sorted(cluster["field_counts"].items(), key=lambda kv: -kv[1])
            ]
            shapes.append(
                {
                    "shape_id": shape_id,
                    "top_level_keys": cluster["top_level_keys"],
                    "row_count": cluster["row_count"],
                    "sample_composition": cluster["composition"],
                    "fields": fields,
                    "example_event": redact_log_event(cluster["example_event"], settings),
                    "correlation_id_candidates": sorted(
                        f for f in cluster["field_counts"] if _CORRELATION_ID_RE.search(f)
                    ),
                }
            )

        total_events = len(tagged_events)
        note = None
        if total_events and parsed == 0:
            note = "Sampled events do not appear to be JSON-structured; field discovery is limited."
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return _aws_error_response(e)

    return {
        "ok": True,
        "data": {
            "log_group": log_group,
            "sampled_events": total_events,
            "parsed_as_json": parsed,
            "sample_composition": {
                "random": len(random_events),
                "error_boosted": total_events - len(random_events),
            },
            "shapes": shapes,
            "note": note,
        },
        "meta": {"shape_count": len(shapes), "sampled_events": total_events},
    }


_BYTES_SCANNED_SAFETY_MARGIN = 0.8  # target 80% of the ceiling, not the ceiling itself


def _build_rows(results: list[list[dict]], settings, include_ptr: bool) -> list[dict]:
    return [
        {
            field["field"]: redact_log_event(field["value"], settings)
            for field in row
            if include_ptr or field["field"] != "@ptr"
        }
        for row in results
    ]


def _suggest_window_seconds(
    bytes_scanned: int, ceiling: int, start_dt: datetime, end_dt: datetime
) -> int | None:
    """Back-solves a window size that would land under the ceiling, from this
    call's observed scan rate — so a caller can retry correctly on the first
    try instead of manually bisecting the time window."""
    requested_window_s = (end_dt - start_dt).total_seconds()
    if requested_window_s <= 0 or bytes_scanned <= 0:
        return None
    scan_rate = bytes_scanned / requested_window_s
    return max(int((ceiling * _BYTES_SCANNED_SAFETY_MARGIN) / scan_rate), 1)


def _run_insights_query(
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
            startTime=_epoch_seconds(start_dt),
            endTime=_epoch_seconds(end_dt),
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
            suggestion = _suggest_window_seconds(
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
        return _aws_error_response(e)

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

    rows = _build_rows(poll_result["results"], settings, include_ptr)
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


@mcp.tool()
def cw_run_insights_query(
    log_groups: list[str],
    query: str,
    start: str,
    end: str,
    limit: int = 100,
    include_ptr: bool = False,
) -> dict:
    """Run a CloudWatch Logs Insights query and wait for results (StartQuery -> poll ->
    GetQueryResults). Logs bytes scanned on every call so cost stays visible.

    `limit` is the authoritative row cap sent to AWS's StartQuery API — set
    it directly (e.g. limit=10 for "last 10 logs") rather than relying on a
    `| limit N` clause inside `query`, which does not reliably cap the
    result count on its own.

    AWS's opaque `@ptr` row pointer (~300 chars, no consumer for it in this
    server today) is stripped from every row by default; pass
    include_ptr=True to keep it."""
    settings = get_settings()
    return _run_insights_query(settings, log_groups, query, start, end, limit, include_ptr)


@mcp.tool()
def cw_get_trace_events(
    log_group: str,
    field: str,
    value: str,
    start: str,
    end: str,
    limit: int = 200,
    include_ptr: bool = False,
) -> dict:
    """Pull every log line in `log_group` where `field` equals `value`, sorted
    chronologically — replaces manually re-running multiple Insights queries to
    reconstruct a trace by hand. Discover `field` via
    cw_describe_log_fields's per-shape `correlation_id_candidates` hint.
    CloudWatch-only: does not fan out to the database or New Relic."""
    settings = get_settings()
    try:
        validate_trace_field(field)
        escaped_value = build_trace_filter_value(value)
    except ToolError as e:
        return e.to_response()
    query = f'fields @timestamp, @message | filter {field} = "{escaped_value}" | sort @timestamp asc'
    response = _run_insights_query(settings, [log_group], query, start, end, limit, include_ptr)
    if response.get("ok"):
        response["data"].update(
            {"log_group": log_group, "field": field, "value": value, "executed_query": query}
        )
    return response


@mcp.tool()
def cw_filter_events(log_group: str, pattern: str, minutes: int = 30) -> dict:
    """Filter raw log events by pattern over the last `minutes`. Cheaper than Insights
    for simple greps."""
    settings = get_settings()
    try:
        validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
        client = get_logs_client(settings)
        end_dt = datetime.now(UTC)
        start_dt, end_dt = clamp_window(
            (end_dt - timedelta(minutes=minutes)).isoformat(),
            end_dt.isoformat(),
            settings.cw_default_window_hours,
            settings.cw_max_window_hours,
        )
        response = client.filter_log_events(
            logGroupName=log_group,
            filterPattern=pattern,
            startTime=_epoch_millis(start_dt),
            endTime=_epoch_millis(end_dt),
            limit=_FILTER_EVENTS_CAP,
        )
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return _aws_error_response(e)

    events = [
        {
            "timestamp": e.get("timestamp"),
            "message": redact_log_event(e.get("message", ""), settings),
            "log_stream": e.get("logStreamName"),
        }
        for e in response.get("events", [])
    ]
    return {
        "ok": True,
        "data": {"log_group": log_group, "events": events, "event_count": len(events)},
        "meta": {"row_count": len(events)},
    }


@mcp.tool()
def cw_get_metric_stats(
    namespace: str,
    metric: str,
    dimensions: dict[str, str],
    period: int,
    start: str,
    end: str,
) -> dict:
    """Get metric datapoints (avg/sum/min/max/sample_count) for correlating error spikes
    with infra metrics like CPU/latency."""
    settings = get_settings()
    try:
        start_dt, end_dt = clamp_window(
            start, end, settings.cw_default_window_hours, settings.cw_max_window_hours
        )
        client = get_metrics_client(settings)
        response = client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric,
            Dimensions=[{"Name": k, "Value": v} for k, v in dimensions.items()],
            StartTime=start_dt,
            EndTime=end_dt,
            Period=period,
            Statistics=["Average", "Sum", "Minimum", "Maximum", "SampleCount"],
        )
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return _aws_error_response(e)

    datapoints = sorted(response.get("Datapoints", []), key=lambda dp: dp["Timestamp"])
    for dp in datapoints:
        dp["Timestamp"] = dp["Timestamp"].isoformat()
    return {
        "ok": True,
        "data": {"namespace": namespace, "metric": metric, "datapoints": datapoints},
        "meta": {"row_count": len(datapoints)},
    }
