from dataclasses import dataclass
from typing import Any

from core.models import DictableMixin


@dataclass
class NrqlRunResult(DictableMixin):
    """Internal-only shape used inside client.py::run_nrql for typed
    construction, then immediately flattened via `.to_dict()` — run_nrql's
    public return type stays a plain dict since it's a constructor-injected
    DI seam (`NewRelicService.__init__`'s `run_nrql_fn`) faked as a dict by
    existing unit tests."""

    results: list[dict[str, Any]]
    metadata: dict[str, Any]


@dataclass
class LogFieldsResult(DictableMixin):
    event_type: str
    window_hours: int
    fields: list[str]
    correlation_id_candidates: list[str]
    note: str | None


@dataclass
class EventTypesResult(DictableMixin):
    window_hours: int
    event_types: list[str]
    note: str | None


@dataclass
class NrqlQueryResult(DictableMixin):
    executed_query: str
    rows: list[dict[str, Any]]
    row_count: int
    metadata: dict[str, Any]
