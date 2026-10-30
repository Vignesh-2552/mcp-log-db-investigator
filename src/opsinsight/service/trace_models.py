from dataclasses import dataclass, field
from typing import Any, Literal

from opsinsight.core.models import DictableMixin

TraceSource = Literal["cloudwatch", "newrelic", "db"]
TRACE_SOURCES: tuple[TraceSource, ...] = ("cloudwatch", "newrelic", "db")


@dataclass
class TimelineEvent(DictableMixin):
    """One entry in the merged timeline. `raw` is the source row, already
    redacted by the source's own service."""

    timestamp: str | None  # ISO 8601 UTC; None for DB rows with no usable time column
    source: TraceSource
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
