import json
from collections import defaultdict
from datetime import UTC, datetime, timedelta

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
    check_bytes_scanned,
    clamp_window,
    poll_query_with_backoff,
    validate_log_groups,
)

logger = get_logger("cloudwatch.tools")

_DESCRIBE_FIELDS_SAMPLE_SIZE = 50
_FILTER_EVENTS_CAP = 1000


def _epoch_seconds(dt: datetime) -> int:
    return int(dt.timestamp())


def _epoch_millis(dt: datetime) -> int:
    return int(dt.timestamp() * 1000)


def _aws_error_response(e: Exception) -> dict:
    logger.error("AWS CloudWatch API error: %s", e)
    return ToolError(rule="aws_error", message="AWS CloudWatch call failed.", detail=str(e)).to_response()


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
    """Sample recent events from a log group and report discovered JSON fields + frequency,
    to ground Logs Insights query generation."""
    settings = get_settings()
    try:
        validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
        client = get_logs_client(settings)
        now = datetime.now(UTC)
        response = client.filter_log_events(
            logGroupName=log_group,
            startTime=_epoch_millis(now - timedelta(hours=1)),
            limit=_DESCRIBE_FIELDS_SAMPLE_SIZE,
        )
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return _aws_error_response(e)

    events = response.get("events", [])
    field_counts: dict[str, int] = defaultdict(int)
    parsed = 0
    for event in events:
        try:
            obj = json.loads(event.get("message", ""))
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            parsed += 1
            for key in obj:
                field_counts[key] += 1

    fields = [
        {"field": k, "frequency": v} for k, v in sorted(field_counts.items(), key=lambda kv: -kv[1])
    ]
    note = None
    if events and parsed == 0:
        note = "Sampled events do not appear to be JSON-structured; field discovery is limited."
    return {
        "ok": True,
        "data": {
            "log_group": log_group,
            "sampled_events": len(events),
            "parsed_as_json": parsed,
            "fields": fields,
            "note": note,
        },
        "meta": {"row_count": len(fields)},
    }


@mcp.tool()
def cw_run_insights_query(
    log_groups: list[str], query: str, start: str, end: str, limit: int = 100
) -> dict:
    """Run a CloudWatch Logs Insights query and wait for results (StartQuery -> poll ->
    GetQueryResults). Logs bytes scanned on every call so cost stays visible.

    `limit` is the authoritative row cap sent to AWS's StartQuery API — set
    it directly (e.g. limit=10 for "last 10 logs") rather than relying on a
    `| limit N` clause inside `query`, which does not reliably cap the
    result count on its own."""
    settings = get_settings()
    query_id: str | None = None
    bytes_scanned: int | None = None
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
            client.stop_query(queryId=query_id)
            check_bytes_scanned(bytes_scanned, settings.cw_max_bytes_scanned)

    except ToolError as e:
        response = e.to_response()
        response["meta"] = {"bytes_scanned": bytes_scanned, "query_id": query_id}
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

    rows = [
        {field["field"]: redact_log_event(field["value"], settings) for field in row}
        for row in poll_result["results"]
    ]
    return {
        "ok": True,
        "data": {
            "status": poll_result["status"],
            "query_id": query_id,
            "rows": rows,
            "row_count": len(rows),
            "bytes_scanned": bytes_scanned,
            "records_matched": poll_result.get("records_matched"),
        },
        "meta": {"row_count": len(rows), "bytes_scanned": bytes_scanned},
    }


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
