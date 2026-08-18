from dataclasses import dataclass
from typing import Any

from core.models import DictableMixin


@dataclass
class LogGroupSummary(DictableMixin):
    log_group: str
    retention_days: int | None
    stored_bytes: int | None


@dataclass
class SampleComposition(DictableMixin):
    random: int
    error_boosted: int


@dataclass
class FieldFrequency(DictableMixin):
    field: str
    frequency: int


@dataclass
class LogFieldShape(DictableMixin):
    shape_id: str
    top_level_keys: list[str]
    row_count: int
    sample_composition: SampleComposition
    fields: list[FieldFrequency]
    example_event: str
    correlation_id_candidates: list[str]


@dataclass
class LogFieldsResult(DictableMixin):
    log_group: str
    sampled_events: int
    parsed_as_json: int
    sample_composition: SampleComposition
    shapes: list[LogFieldShape]
    note: str | None


@dataclass
class QueryPollResult(DictableMixin):
    """Internal-only shape used inside `run_insights_query`'s `poll_fn`
    closure for typed construction, then immediately flattened back to a
    plain dict via `.to_dict()` — `poll_query_with_backoff`'s own signature
    stays a generic `Callable[[], dict] -> dict` since its unit tests feed it
    hand-rolled partial dicts unrelated to this full shape."""

    status: str | None
    results: list[list[dict]]
    bytes_scanned: int
    records_matched: int | None


@dataclass
class LogEvent(DictableMixin):
    timestamp: int | None
    message: str
    log_stream: str | None


@dataclass
class FilterEventsResult(DictableMixin):
    log_group: str
    events: list[LogEvent]
    event_count: int


@dataclass
class MetricStatsResult(DictableMixin):
    namespace: str
    metric: str
    # Raw boto3 Datapoints dicts (AWS-native capitalized keys: Timestamp/
    # Average/Sum/Minimum/Maximum/SampleCount/Unit) — deliberately not
    # modeled per-field; renaming those keys would change the actual tool
    # response shape, not just refactor how it's built.
    datapoints: list[dict[str, Any]]
