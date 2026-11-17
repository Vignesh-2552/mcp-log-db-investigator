import hashlib
import json
import threading
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any

from botocore.exceptions import BotoCoreError, ClientError

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.field_heuristics import CORRELATION_ID_RE as _CORRELATION_ID_RE
from opsinsight.core.limits import fit_rows_to_budget
from opsinsight.core.logging_config import get_logger
from opsinsight.core.redaction import redact_log_event, redact_row
from opsinsight.core.response_cache import NULL_CACHE, ResponseCache, scope_fingerprint
from opsinsight.integrations.cloudwatch.client import get_logs_client
from opsinsight.integrations.cloudwatch.constants import (
    BYTES_SCANNED_SAFETY_MARGIN,
    FILTER_EVENTS_CAP,
    FILTER_EVENTS_MAX_PAGES,
    RUNNING_STATUSES,
)
from opsinsight.integrations.cloudwatch.guardrail import (
    build_trace_filter_value,
    check_bytes_scanned,
    clamp_window,
    poll_query_with_backoff,
    validate_insights_query,
    validate_log_groups,
    validate_trace_field,
)
from opsinsight.integrations.cloudwatch.models import (
    FieldFrequency,
    FilterEventsResult,
    LogEvent,
    LogFieldShape,
    LogFieldsResult,
    LogGroupSummary,
    QueryPollResult,
    SampleComposition,
)
from opsinsight.integrations.cloudwatch.queries import (
    ERROR_BOOST_FILTER_PATTERN,
    build_trace_query,
)
from opsinsight.service.base import BaseService
from opsinsight.service.cloudwatch_common import aws_error_response
from opsinsight.service.ports import LogsClient, LogsClientFactory

logger = get_logger("cloudwatch.logs_service")


class CloudWatchLogsService(BaseService):
    """Business logic for every CloudWatch Logs tool (log groups, field
    discovery, Insights queries, trace lookups, filter patterns). The Logs
    client factory is constructor-injected (defaulting to the real boto3
    factory) so tests can pass a fake client instead of monkeypatching.
    Metrics live in `CloudWatchMetricsService`."""

    def __init__(
        self,
        settings: Settings,
        logs_client_factory: LogsClientFactory = get_logs_client,
        cache: ResponseCache = NULL_CACHE,
    ) -> None:
        super().__init__(settings)
        self._logs_client_factory = logs_client_factory
        self._cache = cache
        # The allowlist and response budget shape responses, so they are part
        # of the scope: changing either never serves an old cached response.
        self._cache_scope = scope_fingerprint(
            "cw",
            settings.cloudwatch_effective_region,
            settings.cloudwatch_effective_access_key_id,
            settings.aws_profile,
            sorted(settings.cw_log_group_allowlist_set),
            settings.cw_max_response_bytes,
            settings.pii_redaction,
        )

    def _logs_client(self) -> LogsClient:
        return self._logs_client_factory(self.settings)

    _aws_error_response = staticmethod(aws_error_response)

    @staticmethod
    def _filter_events_paged(client: LogsClient, limit: int, **kwargs: Any) -> list[dict]:
        """filter_log_events following nextToken until `limit` events, the
        page cap, or the end. A single call can legitimately return zero
        events with a nextToken, which would read as "nothing found"."""
        events: list[dict] = []
        token: str | None = None
        for _ in range(FILTER_EVENTS_MAX_PAGES):
            page_kwargs = dict(kwargs, limit=limit - len(events))
            if token:
                page_kwargs["nextToken"] = token
            resp = client.filter_log_events(**page_kwargs)
            events.extend(resp.get("events", []))
            token = resp.get("nextToken")
            if not token or len(events) >= limit:
                break
        return events[:limit]

    def _budget(self, rows: list[dict]) -> tuple[list[dict], dict]:
        """Applies CW_MAX_RESPONSE_BYTES; returns (kept rows, meta additions)."""
        kept, truncated, _ = fit_rows_to_budget(rows, self.settings.cw_max_response_bytes)
        meta: dict = {"truncated": truncated}
        if truncated:
            meta["truncation_note"] = (
                f"Response capped at {self.settings.cw_max_response_bytes} bytes: returned {len(kept)} "
                f"of {len(rows)} rows. Select fewer fields, aggregate with stats, or narrow the filter."
            )
        return kept, meta

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
        # redact_row, not per-value redact_log_event: an Insights field named
        # e.g. `api_key` or `password` must be masked by name, like DB columns.
        return [
            redact_row(
                {field["field"]: field["value"] for field in row if include_ptr or field["field"] != "@ptr"},
                self.settings,
            )
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
        return self._cache.cached(
            "cw.list_log_groups", self._cache_scope, [prefix], self.settings.cache_metadata_ttl_s,
            lambda: self._list_log_groups(prefix),
        )

    def _list_log_groups(self, prefix: str | None) -> dict:
        settings = self.settings
        allowlist = settings.cw_log_group_allowlist_set
        if not allowlist:
            # Fail closed, matching every other cw_* tool: with no allowlist
            # nothing is queryable, so don't enumerate the account's log groups.
            return self.ok(
                {
                    "log_groups": [],
                    "note": "CLOUDWATCH_ALLOWED_LOG_GROUP is not set, so no log group is accessible.",
                },
                {"row_count": 0},
            )
        try:
            client = self._logs_client()
            kwargs = {"logGroupNamePrefix": prefix} if prefix else {}
            groups = []
            paginator = client.get_paginator("describe_log_groups")
            for page in paginator.paginate(**kwargs):
                for g in page.get("logGroups", []):
                    name = g.get("logGroupName", "")
                    if name not in allowlist:
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
        return self._cache.cached(
            "cw.describe_log_fields", self._cache_scope, [log_group], self.settings.cache_metadata_ttl_s,
            lambda: self._describe_log_fields(log_group),
        )

    def _describe_log_fields(self, log_group: str) -> dict:
        settings = self.settings
        try:
            validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
            client = self._logs_client()
            now = datetime.now(UTC)
            start_time = self._epoch_millis(now - timedelta(hours=1))

            random_events = self._filter_events_paged(
                client, settings.cw_describe_fields_sample_size, logGroupName=log_group, startTime=start_time
            )
            error_events = self._filter_events_paged(
                client,
                settings.cw_describe_fields_error_boost_size,
                logGroupName=log_group,
                startTime=start_time,
                filterPattern=ERROR_BOOST_FILTER_PATTERN,
            )

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
                shape_id = hashlib.sha1(
                    ",".join(cluster["top_level_keys"]).encode(), usedforsecurity=False
                ).hexdigest()[:8]
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
        cancel: threading.Event | None = None,
    ) -> dict:
        """Runs the query, serving a cached result when the window closed at
        least CW_RESULT_CACHE_SETTLE_S ago: logs in a past window don't
        change, and a repeat costs nothing instead of another scan. Only
        `Complete` results are cached."""
        settings = self.settings

        def run() -> dict:
            return self._run_insights_query(log_groups, query, start, end, limit, include_ptr, cancel)

        try:
            start_dt, end_dt = clamp_window(start, end, settings.cw_default_window_hours, settings.cw_max_window_hours)
        except ToolError:
            return run()  # reports the same error, uncached
        if end_dt > datetime.now(UTC) - timedelta(seconds=settings.cw_result_cache_settle_s):
            return run()  # window still open: new events can arrive

        response = self._cache.cached(
            "cw.insights",
            self._cache_scope,
            [sorted(log_groups), query, start_dt.isoformat(), end_dt.isoformat(), limit, include_ptr],
            settings.cw_result_cache_ttl_s,
            run,
            should_store=lambda r: r.get("ok") is True and r["data"].get("status") == "Complete",
        )
        if response.get("meta", {}).get("cache") == "hit":
            # Nothing was scanned (or billed) for this call.
            response["meta"]["bytes_scanned"] = 0
        return response

    def _run_insights_query(
        self,
        log_groups: list[str],
        query: str,
        start: str,
        end: str,
        limit: int,
        include_ptr: bool,
        cancel: threading.Event | None = None,
    ) -> dict:
        """A query still running when polling gives up (CW_POLL_MAX_WAIT_S) is
        stopped: no tool can resume a query_id, and a running query would keep
        scanning (and billing) past the bytes ceiling. Polling also ends early
        once bytes scanned crosses CW_MAX_BYTES_SCANNED. Setting `cancel`
        stops polling and the query itself (used when a caller times out,
        since the thread running this can't be cancelled)."""
        settings = self.settings
        query_id: str | None = None
        bytes_scanned: int | None = None
        start_dt: datetime | None = None
        end_dt: datetime | None = None
        try:
            validate_log_groups(log_groups, settings.cw_log_group_allowlist_set)
            validate_insights_query(query)
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
                if cancel is not None and cancel.is_set():
                    return QueryPollResult(
                        status="Cancelled", results=[], bytes_scanned=0, records_matched=None
                    ).to_dict()
                resp = client.get_query_results(queryId=query_id)
                stats = resp.get("statistics", {})
                return QueryPollResult(
                    status=resp.get("status"),
                    results=resp.get("results", []),
                    bytes_scanned=int(stats.get("bytesScanned", 0)),
                    records_matched=stats.get("recordsMatched"),
                ).to_dict()

            # Waiting on `cancel` instead of sleeping wakes the poll loop as soon as it's set.
            sleep_fn = cancel.wait if cancel is not None else time.sleep
            poll_result = poll_query_with_backoff(
                poll_fn,
                settings.cw_poll_max_wait_s,
                sleep_fn=sleep_fn,
                stop_when=lambda r: r.get("bytes_scanned", 0) > settings.cw_max_bytes_scanned,
            )
            bytes_scanned = poll_result.get("bytes_scanned", 0)

            if poll_result.get("status") == "Cancelled":
                self._stop_query(client, query_id)
                raise ToolError(
                    rule="query_cancelled",
                    message="The caller stopped waiting, so the Insights query was stopped.",
                )

            if bytes_scanned > settings.cw_max_bytes_scanned:
                if poll_result.get("status") in RUNNING_STATUSES:
                    self._stop_query(client, query_id)
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
            self._stop_query(client, query_id)
            return self.ok(
                {
                    "status": "Stopped",
                    "query_id": query_id,
                    "note": f"Query was still running after {settings.cw_poll_max_wait_s}s and was "
                    "stopped. Narrow the window or the log groups and retry.",
                    "bytes_scanned": bytes_scanned,
                },
                {"bytes_scanned": bytes_scanned},
            )

        rows, budget_meta = self._budget(self._build_rows(poll_result["results"], include_ptr))
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
            {"row_count": len(rows), "bytes_scanned": bytes_scanned, **budget_meta},
        )

    @staticmethod
    def _stop_query(client: LogsClient, query_id: str | None) -> None:
        """Best effort: the query may already have finished."""
        if not query_id:
            return
        try:
            client.stop_query(queryId=query_id)
        except (BotoCoreError, ClientError) as e:
            logger.info("stop_query(%s) failed: %s", query_id, e)

    @staticmethod
    def trace_query(field: str, value: str) -> str:
        """The Insights query `get_trace_events` runs. Raises ToolError on a bad field or value."""
        validate_trace_field(field)
        return build_trace_query(field, build_trace_filter_value(value))

    def get_trace_events(
        self,
        log_group: str | list[str],
        field: str,
        value: str,
        start: str,
        end: str,
        limit: int = 200,
        include_ptr: bool = False,
        cancel: threading.Event | None = None,
    ) -> dict:
        # A list searches several groups in one Insights query (one scan, one bill).
        log_groups = [log_group] if isinstance(log_group, str) else list(log_group)
        try:
            query = self.trace_query(field, value)
        except ToolError as e:
            return e.to_response()
        response = self.run_insights_query(
            log_groups, query, start, end, limit, include_ptr, cancel=cancel
        )
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
            raw_events = self._filter_events_paged(
                client,
                FILTER_EVENTS_CAP,
                logGroupName=log_group,
                filterPattern=pattern,
                startTime=self._epoch_millis(start_dt),
                endTime=self._epoch_millis(end_dt),
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
            for e in raw_events
        ]
        kept, budget_meta = self._budget([e.to_dict() for e in events])
        events = events[: len(kept)]
        result = FilterEventsResult(log_group=log_group, events=events, event_count=len(events))
        return self.ok(result.to_dict(), {"row_count": len(events), **budget_meta})
