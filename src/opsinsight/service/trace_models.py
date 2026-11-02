import threading
from collections.abc import Awaitable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Literal

from opsinsight.core.models import DictableMixin


@dataclass
class TimelineEvent(DictableMixin):
    """One entry in the merged timeline. `raw` is the source row, already
    redacted by the source's own service."""

    timestamp: str | None  # ISO 8601 UTC; None for DB rows with no usable time column
    source: str  # the trace source's name, e.g. "cloudwatch"
    origin: str  # log group(s) / NR event type / schema.table
    summary: str
    in_window: bool | None  # None when timestamp is None
    raw: dict[str, Any]


@dataclass
class SourceStatus(DictableMixin):
    status: Literal["ok", "error", "timeout", "skipped", "running"]
    row_count: int = 0
    executed_query: str | None = None
    error: dict[str, Any] | None = None
    reason: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceResult(DictableMixin):
    value: str
    window_start: str
    window_end: str
    timeline: list[TimelineEvent]
    undated: list[TimelineEvent]
    sources: dict[str, SourceStatus]
    notes: list[str]


@dataclass(frozen=True)
class TraceRequest:
    """One `trace_request` call, validated and with its window resolved.
    Per-source options are the caller's overrides (None = use the source's
    configured default)."""

    value: str
    start_dt: datetime
    end_dt: datetime
    max_events: int
    cw_log_groups: list[str] | None = None
    cw_field: str | None = None
    nr_event_type: str | None = None
    nr_field: str | None = None
    db_id_type: str | None = None


@dataclass
class TraceJob:
    """A started source search. `cancel`, when set, is signalled once the
    caller stops waiting (timeout or completion), for work running in a thread
    that can't be cancelled directly."""

    awaitable: Awaitable[dict]
    cancel: threading.Event | None = None


@dataclass
class SourceResult:
    """What a trace source made of a successful response."""

    status: SourceStatus
    events: list[TimelineEvent]
    notes: list[str] = field(default_factory=list)
