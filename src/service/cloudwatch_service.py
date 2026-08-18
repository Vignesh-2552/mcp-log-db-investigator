import hashlib
import json
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from core.config import Settings
from core.errors import ToolError
from core.field_heuristics import CORRELATION_ID_RE as _CORRELATION_ID_RE
from core.logging_config import get_logger
from core.redaction import redact_log_event
from integrations.cloudwatch.client import get_logs_client, get_metrics_client
from integrations.cloudwatch.constants import (
    BYTES_SCANNED_SAFETY_MARGIN,
    FILTER_EVENTS_CAP,
    RUNNING_STATUSES,
)
from integrations.cloudwatch.guardrail import (
    build_trace_filter_value,
    check_bytes_scanned,
    clamp_window,
    poll_query_with_backoff,
    validate_log_groups,
    validate_trace_field,
)
from integrations.cloudwatch.models import (
    FieldFrequency,
    FilterEventsResult,
    LogEvent,
    LogFieldShape,
    LogFieldsResult,
    LogGroupSummary,
    MetricStatsResult,
    QueryPollResult,
    SampleComposition,
)
from integrations.cloudwatch.queries import (
    ERROR_BOOST_FILTER_PATTERN,
    build_trace_query,
)
from service.base import BaseService

logger = get_logger("cloudwatch.service")


class CloudWatchService(BaseService):
    """Business logic for every CloudWatch-backed tool. Log/metric client
    construction is constructor-injected (defaulting to the real boto3
    client factories) so tests can substitute a fake client via the
    constructor instead of monkeypatching module globals — dependency
    inversion instead of patching."""

    def __init__(
        self,
        settings: Settings,
        logs_client_factory: Callable[[Settings], Any] = get_logs_client,
        metrics_client_factory: Callable[[Settings], Any] = get_metrics_client,
    ) -> None:
        super().__init__(settings)
        self._logs_client_factory = logs_client_factory
        self._metrics_client_factory = metrics_client_factory

    def _logs_client(self) -> Any:
        return self._logs_client_factory(self.settings)

    def _metrics_client(self) -> Any:
        return self._metrics_client_factory(self.settings)

    @staticmethod
    def _aws_error_response(e: Exception) -> dict:
        logger.error("AWS CloudWatch API error: %s", e)
        return ToolError(rule="aws_error", message="AWS CloudWatch call failed.", detail=str(e)).to_response()

    @staticmethod
    def _epoch_seconds(dt: datetime) -> int:
        return int(dt.timestamp())

    @staticmethod
    def _epoch_millis(dt: datetime) -> int:
        return int(dt.timestamp() * 1000)

    @classmethod
    def _flatten_field_paths(cls, obj: Any, prefix: str = "", max_depth: int = 4) -> set[str]:
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
                paths |= cls._flatten_field_paths(value, path, max_depth - 1)
        elif isinstance(obj, list) and obj:
            paths |= cls._flatten_field_paths(obj[0], f"{prefix}[]", max_depth - 1)
        return paths

    def _build_rows(self, results: list[list[dict]], include_ptr: bool) -> list[dict]:
        return [
            {
                field["field"]: redact_log_event(field["value"], self.settings)
                for field in row
                if include_ptr or field["field"] != "@ptr"
            }
            for row in results
        ]

    @staticmethod
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
        return max(int((ceiling * BYTES_SCANNED_SAFETY_MARGIN) / scan_rate), 1)

    def list_log_groups(self, prefix: str | None = None) -> dict:
        settings = self.settings
        allowlist = settings.cw_log_group_allowlist_set
        try:
            client = self._logs_client()
            kwargs = {"logGroupNamePrefix": prefix} if prefix else {}
            groups = []
            paginator = client.get_paginator("describe_log_groups")
            for page in paginator.paginate(**kwargs):
                for g in page.get("logGroups", []):
                    name = g.get("logGroupName", "")
                    if allowlist and name not in allowlist:
                        continue
                    groups.append(
                        LogGroupSummary(
                            log_group=name,
                            retention_days=g.get("retentionInDays"),
                            stored_bytes=g.get("storedBytes"),
                        )
                    )
        except (BotoCoreError, ClientError) as e:
            return self._aws_error_response(e)
        return self.ok({"log_groups": [g.to_dict() for g in groups]}, {"row_count": len(groups)})

    def describe_log_fields(self, log_group: str) -> dict:
        settings = self.settings
        try:
            validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
            client = self._logs_client()
            now = datetime.now(UTC)
            start_time = self._epoch_millis(now - timedelta(hours=1))

            random_events = client.filter_log_events(
                logGroupName=log_group,
                startTime=start_time,
                limit=settings.cw_describe_fields_sample_size,
            ).get("events", [])
            error_events = client.filter_log_events(
                logGroupName=log_group,
                startTime=start_time,
                filterPattern=ERROR_BOOST_FILTER_PATTERN,
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
                for path in self._flatten_field_paths(obj):
                    cluster["field_counts"][path] += 1

            shapes: list[LogFieldShape] = []
            for _, cluster in sorted(clusters.items(), key=lambda kv: -kv[1]["row_count"]):
                shape_id = hashlib.sha1(",".join(cluster["top_level_keys"]).encode()).hexdigest()[:8]
                fields = [
                    FieldFrequency(field=k, frequency=v)
                    for k, v in sorted(cluster["field_counts"].items(), key=lambda kv: -kv[1])
                ]
                shapes.append(
                    LogFieldShape(
                        shape_id=shape_id,
                        top_level_keys=cluster["top_level_keys"],
                        row_count=cluster["row_count"],
                        sample_composition=SampleComposition(**cluster["composition"]),
                        fields=fields,
                        example_event=redact_log_event(cluster["example_event"], settings),
                        correlation_id_candidates=sorted(
                            f for f in cluster["field_counts"] if _CORRELATION_ID_RE.search(f)
                        ),
                    )
                )

            total_events = len(tagged_events)
            note = None
            if total_events and parsed == 0:
                note = "Sampled events do not appear to be JSON-structured; field discovery is limited."
        except ToolError as e:
            return e.to_response()
        except (BotoCoreError, ClientError) as e:
            return self._aws_error_response(e)

        result = LogFieldsResult(
            log_group=log_group,
            sampled_events=total_events,
            parsed_as_json=parsed,
            sample_composition=SampleComposition(
                random=len(random_events), error_boosted=total_events - len(random_events)
            ),
            shapes=shapes,
            note=note,
        )
        return self.ok(result.to_dict(), {"shape_count": len(shapes), "sampled_events": total_events})

    def run_insights_query(
        self,
        log_groups: list[str],
        query: str,
        start: str,
        end: str,
        limit: int,
        include_ptr: bool,
    ) -> dict:
        settings = self.settings
        query_id: str | None = None
        bytes_scanned: int | None = None
        start_dt: datetime | None = None
        end_dt: datetime | None = None
        try:
            validate_log_groups(log_groups, settings.cw_log_group_allowlist_set)
            start_dt, end_dt = clamp_window(
                start, end, settings.cw_default_window_hours, settings.cw_max_window_hours
            )
            client = self._logs_client()
            started = client.start_query(
                logGroupNames=log_groups,
                startTime=self._epoch_seconds(start_dt),
                endTime=self._epoch_seconds(end_dt),
                queryString=query,
                limit=limit,
            )
            query_id = started["queryId"]

            def poll_fn() -> dict:
                resp = client.get_query_results(queryId=query_id)
                stats = resp.get("statistics", {})
                return QueryPollResult(
                    status=resp.get("status"),
                    results=resp.get("results", []),
                    bytes_scanned=int(stats.get("bytesScanned", 0)),
                    records_matched=stats.get("recordsMatched"),
                ).to_dict()

            poll_result = poll_query_with_backoff(poll_fn, settings.cw_poll_max_wait_s)
            bytes_scanned = poll_result.get("bytes_scanned", 0)

            if bytes_scanned > settings.cw_max_bytes_scanned:
                if poll_result.get("status") in RUNNING_STATUSES:
                    client.stop_query(queryId=query_id)
                check_bytes_scanned(bytes_scanned, settings.cw_max_bytes_scanned)

        except ToolError as e:
            response = e.to_response()
            meta = {"bytes_scanned": bytes_scanned, "query_id": query_id}
            if e.rule == "bytes_scanned_ceiling_exceeded" and start_dt and end_dt:
                suggestion = self._suggest_window_seconds(
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
            return self._aws_error_response(e)

        if poll_result["status"] in RUNNING_STATUSES:
            return self.ok(
                {
                    "status": "Running",
                    "query_id": query_id,
                    "note": "Query is still running; poll again later with this query_id.",
                    "bytes_scanned": bytes_scanned,
                },
                {"bytes_scanned": bytes_scanned},
            )

        rows = self._build_rows(poll_result["results"], include_ptr)
        return self.ok(
            {
                "status": poll_result["status"],
                "query_id": query_id,
                "rows": rows,
                "row_count": len(rows),
                "bytes_scanned": bytes_scanned,
                "records_matched": poll_result.get("records_matched"),
                "ptr_included": include_ptr,
            },
            {"row_count": len(rows), "bytes_scanned": bytes_scanned},
        )

    def get_trace_events(
        self,
        log_group: str,
        field: str,
        value: str,
        start: str,
        end: str,
        limit: int = 200,
        include_ptr: bool = False,
    ) -> dict:
        try:
            validate_trace_field(field)
            escaped_value = build_trace_filter_value(value)
        except ToolError as e:
            return e.to_response()
        query = build_trace_query(field, escaped_value)
        response = self.run_insights_query([log_group], query, start, end, limit, include_ptr)
        if response.get("ok"):
            response["data"].update(
                {"log_group": log_group, "field": field, "value": value, "executed_query": query}
            )
        return response

    def filter_events(self, log_group: str, pattern: str, minutes: int = 30) -> dict:
        settings = self.settings
        try:
            validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
            client = self._logs_client()
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
                startTime=self._epoch_millis(start_dt),
                endTime=self._epoch_millis(end_dt),
                limit=FILTER_EVENTS_CAP,
            )
        except ToolError as e:
            return e.to_response()
        except (BotoCoreError, ClientError) as e:
            return self._aws_error_response(e)

        events = [
            LogEvent(
                timestamp=e.get("timestamp"),
                message=redact_log_event(e.get("message", ""), settings),
                log_stream=e.get("logStreamName"),
            )
            for e in response.get("events", [])
        ]
        result = FilterEventsResult(log_group=log_group, events=events, event_count=len(events))
        return self.ok(result.to_dict(), {"row_count": len(events)})

    def get_metric_stats(
        self,
        namespace: str,
        metric: str,
        dimensions: dict[str, str],
        period: int,
        start: str,
        end: str,
    ) -> dict:
        settings = self.settings
        try:
            start_dt, end_dt = clamp_window(
                start, end, settings.cw_default_window_hours, settings.cw_max_window_hours
            )
            client = self._metrics_client()
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
            return self._aws_error_response(e)

        datapoints = sorted(response.get("Datapoints", []), key=lambda dp: dp["Timestamp"])
        for dp in datapoints:
            dp["Timestamp"] = dp["Timestamp"].isoformat()
        result = MetricStatsResult(namespace=namespace, metric=metric, datapoints=datapoints)
        return self.ok(result.to_dict(), {"row_count": len(datapoints)})
