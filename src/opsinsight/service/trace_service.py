import asyncio
from collections.abc import Awaitable
from datetime import UTC, date, datetime
from typing import Any

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.logging_config import get_logger
from opsinsight.integrations.cloudwatch.guardrail import clamp_window
from opsinsight.integrations.database.guardrail import fit_rows_to_budget
from opsinsight.service.base import BaseService
from opsinsight.service.cloudwatch_service import CloudWatchService
from opsinsight.service.database_service import DatabaseService
from opsinsight.service.newrelic_service import NewRelicService
from opsinsight.service.trace_models import (
    TRACE_SOURCES,
    SourceStatus,
    TimelineEvent,
    TraceResult,
)

logger = get_logger("trace.service")

SUMMARY_MAX_CHARS = 300

# DB columns tried, in order, for a row's timestamp; then any other *_at column.
DB_TIME_COLUMNS = ("created_at", "occurred_at", "event_time", "timestamp", "logged_at", "updated_at")

NR_MESSAGE_KEYS = ("message", "error.message", "name", "request.uri")

DB_WINDOW_NOTE = (
    "Database rows are not filtered by the time window. Each DB event's `in_window` "
    "says whether its timestamp falls inside it; rows with no time column are in `undated`."
)


def _to_utc(value: Any) -> datetime | None:
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
            return _to_utc(int(text))
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


class TraceService(BaseService):
    """Cross-source trace: looks one identifier up in CloudWatch, New Relic and
    the database at the same time and merges the hits into one timeline.

    Composes the three source services rather than talking to any client
    directly, so every query still goes through that source's guardrail,
    redaction and limits. One source failing or timing out doesn't fail the
    call; its status is reported per source in `data.sources`."""

    def __init__(
        self,
        settings: Settings,
        database_service: DatabaseService,
        cloudwatch_service: CloudWatchService,
        newrelic_service: NewRelicService,
    ) -> None:
        super().__init__(settings)
        self._db = database_service
        self._cw = cloudwatch_service
        self._nr = newrelic_service

    async def trace_request(
        self,
        value: str,
        start: str | None = None,
        end: str | None = None,
        sources: list[str] | None = None,
        cw_log_groups: list[str] | None = None,
        cw_field: str | None = None,
        nr_event_type: str | None = None,
        nr_field: str | None = None,
        db_id_type: str | None = None,
        limit: int = 200,
    ) -> dict:
        settings = self.settings
        if not value or not value.strip():
            return ToolError(rule="empty_value", message="value must not be empty.").to_response()
        requested = list(dict.fromkeys(sources)) if sources else list(TRACE_SOURCES)
        unknown = [s for s in requested if s not in TRACE_SOURCES]
        if unknown:
            return ToolError(
                rule="unknown_source",
                message=f"Unknown source(s): {', '.join(unknown)}.",
                allowed=list(TRACE_SOURCES),
            ).to_response()
        try:
            start_dt, end_dt = clamp_window(
                start,
                end,
                min(settings.cw_default_window_hours, settings.nr_default_window_hours),
                min(settings.cw_max_window_hours, settings.nr_max_window_hours),
            )
        except ToolError as e:
            return e.to_response()

        max_events = max(1, min(limit, settings.trace_max_events))
        log_groups = cw_log_groups or settings.trace_cw_log_groups_list
        cw_field = cw_field or settings.trace_cw_field
        nr_event_type = nr_event_type or settings.trace_nr_event_type
        nr_field = nr_field or settings.trace_nr_field
        db_id_type = db_id_type or settings.trace_db_id_type

        statuses: dict[str, SourceStatus] = {}
        jobs: dict[str, Awaitable[dict]] = {}
        for source in TRACE_SOURCES:
            if source not in requested:
                statuses[source] = SourceStatus(status="skipped", reason="not requested")
            elif source == "cloudwatch" and not log_groups:
                statuses[source] = SourceStatus(
                    status="skipped",
                    reason="no log groups: pass cw_log_groups or set TRACE_CW_LOG_GROUPS / "
                    "CLOUDWATCH_ALLOWED_LOG_GROUP",
                )
            elif source == "newrelic" and not (settings.new_relic_api_key and settings.new_relic_account_id):
                statuses[source] = SourceStatus(status="skipped", reason="New Relic is not configured")
            elif source == "db" and not db_id_type:
                statuses[source] = SourceStatus(
                    status="skipped",
                    reason="no id type: pass db_id_type (e.g. 'order_id') or set TRACE_DB_ID_TYPE",
                )
            elif source == "cloudwatch":
                jobs[source] = asyncio.to_thread(
                    self._cw.get_trace_events,
                    log_groups,
                    cw_field,
                    value,
                    start_dt.isoformat(),
                    end_dt.isoformat(),
                    max_events,
                )
            elif source == "newrelic":
                jobs[source] = self._nr.get_trace_events(
                    nr_event_type, nr_field, value, start_dt, end_dt, max_events
                )
            else:
                jobs[source] = self._db.search_by_identifier(value, db_id_type)

        if not jobs:
            return ToolError(
                rule="no_sources_available",
                message="No source could be searched.",
                detail="; ".join(f"{k}: {v.reason}" for k, v in statuses.items()),
            ).to_response()

        names = list(jobs)
        responses = await asyncio.gather(*(self._run_bounded(name, jobs[name]) for name in names))

        events: list[TimelineEvent] = []
        notes: list[str] = []
        for name, response in zip(names, responses, strict=True):
            status, source_events = self._collect(name, response, log_groups, start_dt, end_dt)
            statuses[name] = status
            events.extend(source_events)
            if name == "db" and status.status == "ok":
                notes.append(DB_WINDOW_NOTE)
                if response["data"].get("data_freshness_note"):
                    notes.append(response["data"]["data_freshness_note"])

        attempted = [statuses[n] for n in names]
        if all(s.status in ("error", "timeout") for s in attempted):
            return {
                "ok": False,
                "error": {
                    "rule": "all_sources_failed",
                    "message": "Every searched source failed.",
                    "detail": "See meta.sources for each source's error.",
                    "allowed": None,
                },
                "meta": {"sources": {k: v.to_dict() for k, v in statuses.items()}},
            }

        dated = sorted(
            (e for e in events if e.timestamp is not None),
            key=lambda e: datetime.fromisoformat(e.timestamp),  # type: ignore[arg-type]
        )
        undated = [e for e in events if e.timestamp is None]
        timeline, undated, truncated, total = self._fit(dated, undated, max_events)
        if truncated:
            notes.append(
                f"Returned {len(timeline) + len(undated)} of {total} events (earliest first). "
                "Narrow the window or limit `sources` to see the rest."
            )

        result = TraceResult(
            value=value,
            window_start=start_dt.isoformat(),
            window_end=end_dt.isoformat(),
            timeline=timeline,
            undated=undated,
            sources=statuses,
            notes=notes,
        )
        return self.ok(
            result.to_dict(),
            {
                "event_count": len(timeline) + len(undated),
                "truncated": truncated,
                "bytes_scanned": statuses["cloudwatch"].extra.get("bytes_scanned"),
            },
        )

    async def _run_bounded(self, name: str, job: Awaitable[dict]) -> dict:
        """Runs one source with the per-source timeout. Never raises: failures
        come back as error responses so the other sources still report."""
        timeout = self.settings.trace_source_timeout_s
        try:
            return await asyncio.wait_for(job, timeout)
        except TimeoutError:
            # A CloudWatch thread can't be cancelled; it stops at CW_POLL_MAX_WAIT_S.
            return {"ok": False, "timeout": True, "error": {"rule": "source_timeout",
                    "message": f"{name} did not answer within {timeout:g}s."}}
        except ToolError as e:
            return e.to_response()
        except Exception as e:
            logger.exception("trace_request: %s failed", name)
            return {"ok": False, "error": {"rule": "source_failed",
                    "message": f"{name} failed: {type(e).__name__}."}}

    def _collect(
        self,
        name: str,
        response: dict,
        log_groups: list[str],
        start_dt: datetime,
        end_dt: datetime,
    ) -> tuple[SourceStatus, list[TimelineEvent]]:
        if not response.get("ok"):
            status = "timeout" if response.get("timeout") else "error"
            extra = {k: v for k, v in (response.get("meta") or {}).items() if v is not None}
            return SourceStatus(status=status, error=response.get("error"), extra=extra), []

        data = response["data"]
        if name == "cloudwatch":
            extra = {"bytes_scanned": data.get("bytes_scanned"), "log_groups": log_groups}
            if data.get("status") == "Running":
                return SourceStatus(
                    status="running",
                    reason="Insights query was still running when polling stopped "
                    f"(query_id {data.get('query_id')}); retry with a narrower window.",
                    extra=extra,
                ), []
            origin = ", ".join(log_groups)
            events = [
                self._event("cloudwatch", row.get("@log", origin), row.get("@timestamp"),
                            row.get("@message", row), row, start_dt, end_dt)
                for row in data.get("rows", [])
            ]
            return SourceStatus(
                status="ok", row_count=len(events), executed_query=data.get("executed_query"), extra=extra
            ), events

        if name == "newrelic":
            event_type = data.get("event_type", "")
            events = []
            for row in data.get("rows", []):
                message = next((row[k] for k in NR_MESSAGE_KEYS if row.get(k) is not None), row)
                events.append(self._event("newrelic", event_type, row.get("timestamp"), message, row,
                                          start_dt, end_dt))
            return SourceStatus(status="ok", row_count=len(events), executed_query=data.get("executed_query")), events

        events = []
        for match in data.get("matches", []):
            for row in match.get("rows", []):
                ts = self._db_row_time(row)
                summary = f"{match['table']} row where {match['column']} matched"
                events.append(self._event("db", match["table"], ts, summary, row, start_dt, end_dt))
        return SourceStatus(
            status="ok",
            row_count=len(events),
            extra={
                "tables": [m["table"] for m in data.get("matches", [])],
                "truncated": bool((response.get("meta") or {}).get("truncated")),
            },
        ), events

    @staticmethod
    def _db_row_time(row: dict[str, Any]) -> Any:
        for column in DB_TIME_COLUMNS:
            if row.get(column) is not None:
                return row[column]
        for column, value in row.items():
            if column.endswith("_at") and value is not None and _to_utc(value) is not None:
                return value
        return None

    @staticmethod
    def _event(
        source: str,
        origin: str,
        raw_time: Any,
        message: Any,
        row: dict[str, Any],
        start_dt: datetime,
        end_dt: datetime,
    ) -> TimelineEvent:
        ts = _to_utc(raw_time)
        return TimelineEvent(
            timestamp=ts.isoformat() if ts else None,
            source=source,  # type: ignore[arg-type]
            origin=str(origin),
            summary=_summary(message),
            in_window=(start_dt <= ts <= end_dt) if ts else None,
            raw=row,
        )

    def _fit(
        self, dated: list[TimelineEvent], undated: list[TimelineEvent], max_events: int
    ) -> tuple[list[TimelineEvent], list[TimelineEvent], bool, int]:
        """Applies the event-count cap, then the byte budget, earliest first."""
        total = len(dated) + len(undated)
        ordered = (dated + undated)[:max_events]
        kept, over_budget, _ = fit_rows_to_budget(
            [e.to_dict() for e in ordered], self.settings.trace_max_response_bytes
        )
        kept_events = ordered[: len(kept)]
        n_dated = min(len(dated), len(kept_events))
        truncated = over_budget or len(kept_events) < total
        return kept_events[:n_dated], kept_events[n_dated:], truncated, total
