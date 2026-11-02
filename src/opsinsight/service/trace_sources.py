"""Trace source adapters: how `trace_request` searches one data source.

`TraceService` only knows the `TraceSource` Protocol. Each adapter wraps one
source *service* (never a client), so every query still goes through that
source's guardrail, redaction and limits. Adding a source to `trace_request`
means writing one adapter and registering it in `default_trace_sources` —
`TraceService` itself does not change.
"""

import asyncio
import threading
from collections.abc import Callable
from datetime import UTC, date, datetime
from typing import Any, Protocol

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.integrations.database.constants import TIME_COLUMN_PREFERENCE
from opsinsight.service.cloudwatch_logs_service import CloudWatchLogsService
from opsinsight.service.database_service import DatabaseService
from opsinsight.service.newrelic_service import NewRelicService
from opsinsight.service.trace_models import (
    SourceResult,
    SourceStatus,
    TimelineEvent,
    TraceJob,
    TraceRequest,
)

SUMMARY_MAX_CHARS = 300

NR_MESSAGE_KEYS = ("message", "error.message", "name", "request.uri")

DB_WINDOW_NOTE = (
    "Database rows are not filtered by the time window. Each DB event's `in_window` "
    "says whether its timestamp falls inside it; rows with no time column are in `undated`."
)


class TraceSource(Protocol):
    """One searchable source for `trace_request`."""

    name: str

    def skip_reason(self, request: TraceRequest) -> str | None:
        """Why this source can't be searched for `request` (e.g. not
        configured), or None when it can."""
        ...

    def planned_query(self, request: TraceRequest) -> str | None:
        """The query this source will run, known up front so a timeout or
        error can still report it. None when inputs are invalid (the search
        itself then returns the guardrail error) or there is no single query."""
        ...

    def start(self, request: TraceRequest) -> TraceJob:
        """Starts the search. The job's awaitable resolves to the source
        service's standard `{"ok": ..., ...}` response."""
        ...

    def collect(self, response: dict, request: TraceRequest) -> SourceResult:
        """Turns a successful (`ok: True`) response into timeline events."""
        ...


# --- shared helpers -----------------------------------------------------------


def to_utc(value: Any) -> datetime | None:
    """Best-effort conversion of a source timestamp to an aware UTC datetime."""
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day, tzinfo=UTC)
    if isinstance(value, (int, float)):
        # Epoch millis (New Relic) vs seconds.
        seconds = value / 1000 if value > 10_000_000_000 else value
        try:
            return datetime.fromtimestamp(seconds, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if isinstance(value, str):
        text = value.strip()
        if text.isdigit():
            return to_utc(int(text))
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _summary(value: Any) -> str:
    text = value if isinstance(value, str) else str(value)
    text = " ".join(text.split())
    return text if len(text) <= SUMMARY_MAX_CHARS else text[: SUMMARY_MAX_CHARS - 1] + "…"


def timeline_event(
    source: str,
    origin: str,
    raw_time: Any,
    message: Any,
    row: dict[str, Any],
    request: TraceRequest,
) -> TimelineEvent:
    ts = to_utc(raw_time)
    return TimelineEvent(
        timestamp=ts.isoformat() if ts else None,
        source=source,
        origin=str(origin),
        summary=_summary(message),
        in_window=(request.start_dt <= ts <= request.end_dt) if ts else None,
        raw=row,
    )


def _query_or_none(build: Callable[..., str], *args: Any) -> str | None:
    try:
        return build(*args)
    except ToolError:
        return None


# --- adapters -----------------------------------------------------------------


class CloudWatchTraceSource:
    name = "cloudwatch"

    def __init__(self, settings: Settings, logs_service: CloudWatchLogsService) -> None:
        self._settings = settings
        self._logs = logs_service

    def _log_groups(self, request: TraceRequest) -> list[str]:
        return request.cw_log_groups or self._settings.trace_cw_log_groups_list

    def _field(self, request: TraceRequest) -> str:
        return request.cw_field or self._settings.trace_cw_field

    def skip_reason(self, request: TraceRequest) -> str | None:
        if not self._log_groups(request):
            return (
                "no log groups: pass cw_log_groups or set TRACE_CW_LOG_GROUPS / "
                "CLOUDWATCH_ALLOWED_LOG_GROUP"
            )
        return None

    def planned_query(self, request: TraceRequest) -> str | None:
        return _query_or_none(self._logs.trace_query, self._field(request), request.value)

    def start(self, request: TraceRequest) -> TraceJob:
        # Nothing resumes a trace's query_id, so a query still running when
        # polling ends (or when the caller times out) is stopped.
        cancel = threading.Event()
        awaitable = asyncio.to_thread(
            self._logs.get_trace_events,
            self._log_groups(request),
            self._field(request),
            request.value,
            request.start_dt.isoformat(),
            request.end_dt.isoformat(),
            request.max_events,
            stop_if_running=True,
            cancel=cancel,
        )
        return TraceJob(awaitable, cancel)

    def collect(self, response: dict, request: TraceRequest) -> SourceResult:
        data = response["data"]
        log_groups = self._log_groups(request)
        extra = {"bytes_scanned": data.get("bytes_scanned"), "log_groups": log_groups}
        if data.get("status") in ("Running", "Stopped"):
            extra["query_id"] = data.get("query_id")
            status = SourceStatus(
                status="running",
                reason="Insights query did not finish in time and was stopped; "
                "retry with a narrower window or fewer log groups.",
                executed_query=data.get("executed_query"),
                extra=extra,
            )
            return SourceResult(status, [])
        origin = ", ".join(log_groups)
        events = [
            timeline_event(self.name, row.get("@log", origin), row.get("@timestamp"),
                           row.get("@message", row), row, request)
            for row in data.get("rows", [])
        ]
        status = SourceStatus(
            status="ok", row_count=len(events), executed_query=data.get("executed_query"), extra=extra
        )
        return SourceResult(status, events)


class NewRelicTraceSource:
    name = "newrelic"

    def __init__(self, settings: Settings, newrelic_service: NewRelicService) -> None:
        self._settings = settings
        self._nr = newrelic_service

    def _event_type(self, request: TraceRequest) -> str:
        return request.nr_event_type or self._settings.trace_nr_event_type

    def _field(self, request: TraceRequest) -> str:
        return request.nr_field or self._settings.trace_nr_field

    def skip_reason(self, request: TraceRequest) -> str | None:
        if not (self._settings.new_relic_api_key and self._settings.new_relic_account_id):
            return "New Relic is not configured"
        return None

    def planned_query(self, request: TraceRequest) -> str | None:
        return _query_or_none(
            self._nr.trace_query, self._event_type(request), self._field(request),
            request.value, request.start_dt, request.end_dt,
        )

    def start(self, request: TraceRequest) -> TraceJob:
        return TraceJob(
            self._nr.get_trace_events(
                self._event_type(request), self._field(request), request.value,
                request.start_dt, request.end_dt, request.max_events,
            )
        )

    def collect(self, response: dict, request: TraceRequest) -> SourceResult:
        data = response["data"]
        event_type = data.get("event_type", "")
        events = []
        for row in data.get("rows", []):
            message = next((row[k] for k in NR_MESSAGE_KEYS if row.get(k) is not None), row)
            events.append(timeline_event(self.name, event_type, row.get("timestamp"), message, row, request))
        status = SourceStatus(status="ok", row_count=len(events), executed_query=data.get("executed_query"))
        return SourceResult(status, events)


class DatabaseTraceSource:
    name = "db"

    def __init__(self, settings: Settings, database_service: DatabaseService) -> None:
        self._settings = settings
        self._db = database_service

    def _id_type(self, request: TraceRequest) -> str | None:
        return request.db_id_type or self._settings.trace_db_id_type

    def skip_reason(self, request: TraceRequest) -> str | None:
        if not self._id_type(request):
            return "no id type: pass db_id_type (e.g. 'order_id') or set TRACE_DB_ID_TYPE"
        return None

    def planned_query(self, request: TraceRequest) -> str | None:
        return None  # one query per matched table, reported after the search

    def start(self, request: TraceRequest) -> TraceJob:
        # Newest rows per table first, at most one timeline's worth.
        return TraceJob(
            self._db.search_by_identifier(request.value, self._id_type(request), limit=request.max_events)
        )

    @staticmethod
    def _row_time(row: dict[str, Any]) -> Any:
        for column in TIME_COLUMN_PREFERENCE:
            if row.get(column) is not None:
                return row[column]
        for column, value in row.items():
            if column.endswith("_at") and value is not None and to_utc(value) is not None:
                return value
        return None

    def collect(self, response: dict, request: TraceRequest) -> SourceResult:
        data = response["data"]
        matches = data.get("matches", [])
        events = []
        for match in matches:
            for row in match.get("rows", []):
                summary = f"{match['table']} row where {match['column']} matched"
                events.append(timeline_event(self.name, match["table"], self._row_time(row), summary, row, request))
        executed = [m["executed_sql"] for m in matches if m.get("executed_sql")]
        status = SourceStatus(
            status="ok",
            row_count=len(events),
            executed_query=";\n".join(executed) or None,
            extra={
                "tables": [m["table"] for m in matches],
                "truncated": bool((response.get("meta") or {}).get("truncated")),
            },
        )
        notes = [DB_WINDOW_NOTE]
        if data.get("data_freshness_note"):
            notes.append(data["data_freshness_note"])
        return SourceResult(status, events, notes)


def default_trace_sources(
    settings: Settings,
    database_service: DatabaseService,
    cloudwatch_logs_service: CloudWatchLogsService,
    newrelic_service: NewRelicService,
) -> list[TraceSource]:
    """The sources `trace_request` searches, in the order they are reported."""
    return [
        CloudWatchTraceSource(settings, cloudwatch_logs_service),
        NewRelicTraceSource(settings, newrelic_service),
        DatabaseTraceSource(settings, database_service),
    ]
