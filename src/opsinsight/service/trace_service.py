import asyncio
import threading
from collections.abc import Awaitable, Sequence
from datetime import datetime

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.limits import fit_rows_to_budget
from opsinsight.core.logging_config import get_logger
from opsinsight.core.time_window import clamp_window
from opsinsight.service.base import BaseService
from opsinsight.service.trace_models import (
    SourceStatus,
    TimelineEvent,
    TraceJob,
    TraceRequest,
    TraceResult,
)
from opsinsight.service.trace_sources import TraceSource

logger = get_logger("trace.service")


class TraceService(BaseService):
    """Backs `trace_request`: searches every registered `TraceSource` in
    parallel for one identifier and merges the hits into one timeline.

    Knows nothing source-specific — each source's search, skip rules and
    result parsing live in its adapter (`service/trace_sources.py`). One
    source failing or timing out doesn't fail the call; its status is
    reported per source in `data.sources`."""

    def __init__(self, settings: Settings, sources: Sequence[TraceSource]) -> None:
        super().__init__(settings)
        names = [s.name for s in sources]
        if len(set(names)) != len(names):
            raise ValueError(f"Duplicate trace source names: {names}")
        self._sources = list(sources)

    @property
    def source_names(self) -> list[str]:
        return [s.name for s in self._sources]

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
        known = self.source_names
        requested = list(dict.fromkeys(sources)) if sources else known
        unknown = [s for s in requested if s not in known]
        if unknown:
            return ToolError(
                rule="unknown_source",
                message=f"Unknown source(s): {', '.join(unknown)}.",
                allowed=known,
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

        request = TraceRequest(
            value=value,
            start_dt=start_dt,
            end_dt=end_dt,
            max_events=max(1, min(limit, settings.trace_max_events)),
            cw_log_groups=cw_log_groups,
            cw_field=cw_field,
            nr_event_type=nr_event_type,
            nr_field=nr_field,
            db_id_type=db_id_type,
        )

        statuses: dict[str, SourceStatus] = {}
        jobs: dict[str, TraceJob] = {}
        queries: dict[str, str | None] = {}
        active: dict[str, TraceSource] = {}
        for source in self._sources:
            if source.name not in requested:
                statuses[source.name] = SourceStatus(status="skipped", reason="not requested")
            elif reason := source.skip_reason(request):
                statuses[source.name] = SourceStatus(status="skipped", reason=reason)
            else:
                queries[source.name] = source.planned_query(request)
                jobs[source.name] = source.start(request)
                active[source.name] = source

        if not jobs:
            return ToolError(
                rule="no_sources_available",
                message="No source could be searched.",
                detail="; ".join(f"{k}: {v.reason}" for k, v in statuses.items()),
            ).to_response()

        names = list(jobs)
        responses = await asyncio.gather(
            *(self._run_bounded(name, jobs[name].awaitable, jobs[name].cancel) for name in names)
        )

        events: list[TimelineEvent] = []
        notes: list[str] = []
        for name, response in zip(names, responses, strict=True):
            if response.get("ok"):
                result = active[name].collect(response, request)
                status = result.status
                events.extend(result.events)
                notes.extend(result.notes)
            else:
                status = self._failure_status(response)
            if status.executed_query is None:
                status.executed_query = queries.get(name)
            statuses[name] = status

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

        timeline, undated, dropped = self._fit(events, request.max_events, start_dt, end_dt)
        truncated = bool(dropped)
        if truncated:
            notes.append(self._truncation_note(len(timeline) + len(undated), len(events), dropped))

        result = TraceResult(
            value=value,
            window_start=start_dt.isoformat(),
            window_end=end_dt.isoformat(),
            timeline=timeline,
            undated=undated,
            sources=statuses,
            notes=notes,
        )
        bytes_scanned = next(
            (s.extra["bytes_scanned"] for s in statuses.values() if s.extra.get("bytes_scanned") is not None),
            None,
        )
        return self.ok(
            result.to_dict(),
            {
                "event_count": len(timeline) + len(undated),
                "truncated": truncated,
                "bytes_scanned": bytes_scanned,
            },
        )

    @staticmethod
    def _failure_status(response: dict) -> SourceStatus:
        status = "timeout" if response.get("timeout") else "error"
        extra = {k: v for k, v in (response.get("meta") or {}).items() if v is not None}
        return SourceStatus(status=status, error=response.get("error"), extra=extra)

    async def _run_bounded(
        self, name: str, job: Awaitable[dict], cancel: threading.Event | None = None
    ) -> dict:
        """Runs one source with the per-source timeout. Never raises: failures
        come back as error responses so the other sources still report."""
        timeout = self.settings.trace_source_timeout_s
        try:
            return await asyncio.wait_for(job, timeout)
        except TimeoutError:
            message = f"{name} did not answer within {timeout:g}s."
            if cancel is not None:
                message += " Its query was stopped."
            return {"ok": False, "timeout": True, "error": {"rule": "source_timeout", "message": message}}
        except ToolError as e:
            return e.to_response()
        except Exception as e:
            logger.exception("trace_request: %s failed", name)
            return {"ok": False, "error": {"rule": "source_failed",
                    "message": f"{name} failed: {type(e).__name__}."}}
        finally:
            # A thread can't be cancelled; this tells it to stop polling and
            # stop its query. Harmless when the job already finished.
            if cancel is not None:
                cancel.set()

    def _fit(
        self,
        events: list[TimelineEvent],
        max_events: int,
        start_dt: datetime,
        end_dt: datetime,
    ) -> tuple[list[TimelineEvent], list[TimelineEvent], list[TimelineEvent]]:
        """Applies the event-count cap, then the byte budget. In-window events
        are kept first (earliest first), then undated rows, then out-of-window
        rows (closest to the window first), so old DB rows can't push this
        window's logs out. Returns (timeline, undated, dropped)."""

        def when(e: TimelineEvent) -> datetime:
            return datetime.fromisoformat(e.timestamp)  # type: ignore[arg-type]

        def distance(e: TimelineEvent) -> float:
            ts = when(e)
            return (start_dt - ts).total_seconds() if ts < start_dt else (ts - end_dt).total_seconds()

        in_window = sorted((e for e in events if e.in_window), key=when)
        undated = [e for e in events if e.timestamp is None]
        outside = sorted((e for e in events if e.in_window is False), key=distance)
        priority = in_window + undated + outside

        capped = priority[:max_events]
        kept, _, _ = fit_rows_to_budget([e.to_dict() for e in capped], self.settings.trace_max_response_bytes)
        kept_events = capped[: len(kept)]
        timeline = sorted((e for e in kept_events if e.timestamp is not None), key=when)
        kept_undated = [e for e in kept_events if e.timestamp is None]
        return timeline, kept_undated, priority[len(kept_events) :]

    @staticmethod
    def _truncation_note(returned: int, total: int, dropped: list[TimelineEvent]) -> str:
        note = f"Returned {returned} of {total} events."
        dropped_in_window = sum(1 for e in dropped if e.in_window)
        dropped_other = len(dropped) - dropped_in_window
        if dropped_in_window:
            note += (
                f" {dropped_in_window} later in-window events were dropped (earliest are kept). "
                "Narrow the window or limit `sources` to see the rest."
            )
        if dropped_other:
            note += (
                f" {dropped_other} events outside the window or without a timestamp were dropped. "
                "The window doesn't filter the database search; use db_search_by_identifier "
                "to see those rows."
            )
        return note
