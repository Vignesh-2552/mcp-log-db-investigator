import json
from datetime import UTC, datetime, timedelta

import pytest

from core.config import get_settings
from tools.cloudwatch import utils as cw_utils
from tools.cloudwatch.cw_describe_log_fields import cw_describe_log_fields
from tools.cloudwatch.cw_get_trace_events import cw_get_trace_events
from tools.cloudwatch.cw_run_insights_query import cw_run_insights_query

LOG_GROUP = "/aws/ecs/test-svc"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDWATCH_ALLOWED_LOG_GROUP", LOG_GROUP)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeLogsClient:
    """Fakes filter_log_events: returns error_events when a filterPattern is
    supplied (the error-boost call), otherwise random_events."""

    def __init__(self, random_events, error_events=None):
        self.random_events = random_events
        self.error_events = error_events or []
        self.calls: list[dict] = []

    def filter_log_events(self, **kwargs):
        self.calls.append(kwargs)
        if "filterPattern" in kwargs:
            return {"events": self.error_events}
        return {"events": self.random_events}


def _event(event_id: str, obj: dict) -> dict:
    return {"eventId": event_id, "message": json.dumps(obj)}


def test_describe_log_fields_clusters_by_shape(monkeypatch: pytest.MonkeyPatch):
    random_events = [
        _event(f"r{i}", {"level": "info", "method": "GET", "referer": "x"}) for i in range(3)
    ] + [_event("r-err", {"message": {"user": {"id": "u1"}}})]
    error_events = [_event("e1", {"message": {"user": {"id": "u2"}, "errors": [{"path": "x"}]}})]
    fake_client = _FakeLogsClient(random_events, error_events)
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)

    result = cw_describe_log_fields(LOG_GROUP)

    assert result["ok"] is True
    shapes = result["data"]["shapes"]
    assert len(shapes) == 2
    # Larger cluster (the access-log shape, 3 rows) sorts first.
    access_shape = shapes[0]
    assert access_shape["row_count"] == 3
    assert sorted(access_shape["top_level_keys"]) == ["level", "method", "referer"]
    assert access_shape["sample_composition"] == {"random": 3, "error_boosted": 0}

    error_shape = shapes[1]
    assert error_shape["row_count"] == 2
    assert error_shape["sample_composition"] == {"random": 1, "error_boosted": 1}
    field_names = {f["field"] for f in error_shape["fields"]}
    assert "message.user.id" in field_names
    assert "message.errors[].path" in field_names

    assert result["data"]["sample_composition"] == {"random": 4, "error_boosted": 1}
    assert result["meta"]["shape_count"] == 2


def test_describe_log_fields_flags_correlation_id_candidates(monkeypatch: pytest.MonkeyPatch):
    events = [_event("r1", {"trace_id": "abc", "level": "info"})]
    fake_client = _FakeLogsClient(events)
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)

    result = cw_describe_log_fields(LOG_GROUP)

    assert result["data"]["shapes"][0]["correlation_id_candidates"] == ["trace_id"]


def test_describe_log_fields_empty_sample_note(monkeypatch: pytest.MonkeyPatch):
    fake_client = _FakeLogsClient([{"eventId": "r1", "message": "not json"}])
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)

    result = cw_describe_log_fields(LOG_GROUP)

    assert result["data"]["shapes"] == []
    assert result["data"]["note"] is not None


class _FakeInsightsClient:
    def __init__(self, results, bytes_scanned, status="Complete", records_matched=None):
        self.results = results
        self.bytes_scanned = bytes_scanned
        self.status = status
        self.records_matched = records_matched
        self.stopped = False

    def start_query(self, **kwargs):
        return {"queryId": "q-1"}

    def get_query_results(self, queryId):
        return {
            "status": self.status,
            "results": self.results,
            "statistics": {"bytesScanned": self.bytes_scanned, "recordsMatched": self.records_matched},
        }

    def stop_query(self, queryId):
        self.stopped = True


def _window():
    end = datetime.now(UTC)
    start = end - timedelta(hours=1)
    return start.isoformat(), end.isoformat()


def test_run_insights_query_strips_ptr_by_default(monkeypatch: pytest.MonkeyPatch):
    results = [[{"field": "@ptr", "value": "opaque"}, {"field": "@timestamp", "value": "t1"}]]
    fake_client = _FakeInsightsClient(results, bytes_scanned=100)
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)
    start, end = _window()

    result = cw_run_insights_query([LOG_GROUP], "fields @timestamp", start, end)

    assert result["ok"] is True
    assert result["data"]["rows"] == [{"@timestamp": "t1"}]
    assert result["data"]["ptr_included"] is False


def test_run_insights_query_keeps_ptr_when_requested(monkeypatch: pytest.MonkeyPatch):
    results = [[{"field": "@ptr", "value": "opaque"}, {"field": "@timestamp", "value": "t1"}]]
    fake_client = _FakeInsightsClient(results, bytes_scanned=100)
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)
    start, end = _window()

    result = cw_run_insights_query(
        [LOG_GROUP], "fields @timestamp", start, end, include_ptr=True
    )

    assert result["data"]["rows"] == [{"@ptr": "opaque", "@timestamp": "t1"}]
    assert result["data"]["ptr_included"] is True


def test_run_insights_query_ceiling_exceeded_suggests_window(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CW_MAX_BYTES_SCANNED", "5000000000")
    get_settings.cache_clear()
    fake_client = _FakeInsightsClient(results=[], bytes_scanned=10_000_000_000)
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)
    start, end = _window()

    result = cw_run_insights_query([LOG_GROUP], "fields @timestamp", start, end)

    assert result["ok"] is False
    assert result["error"]["rule"] == "bytes_scanned_ceiling_exceeded"
    assert result["meta"]["suggested_max_window_seconds"] > 0
    assert "suggested_max_window_seconds" not in result["error"]  # lives in meta, not the error payload
    assert str(result["meta"]["suggested_max_window_seconds"]) in result["error"]["detail"]


@pytest.mark.parametrize(
    ("bytes_scanned", "ceiling", "window_seconds", "expected_max"),
    [
        (10_000_000_000, 5_000_000_000, 3600, 1440),  # scan_rate ~2.78MB/s -> ~1440s at 80% margin
    ],
)
def test_suggest_window_seconds_formula(bytes_scanned, ceiling, window_seconds, expected_max):
    start = datetime.now(UTC)
    end = start + timedelta(seconds=window_seconds)
    suggestion = cw_utils.suggest_window_seconds(bytes_scanned, ceiling, start, end)
    assert suggestion == expected_max


def test_suggest_window_seconds_returns_none_for_degenerate_input():
    now = datetime.now(UTC)
    assert cw_utils.suggest_window_seconds(0, 5000, now, now + timedelta(hours=1)) is None
    assert cw_utils.suggest_window_seconds(100, 5000, now, now) is None


def test_get_trace_events_rejects_invalid_field():
    start, end = _window()
    result = cw_get_trace_events(LOG_GROUP, "bad field!", "x", start, end)
    assert result["ok"] is False
    assert result["error"]["rule"] == "invalid_field_name"


def test_get_trace_events_rejects_newline_in_value():
    start, end = _window()
    result = cw_get_trace_events(LOG_GROUP, "trace_id", "line1\nline2", start, end)
    assert result["ok"] is False
    assert result["error"]["rule"] == "invalid_trace_value"


def test_get_trace_events_builds_and_executes_query(monkeypatch: pytest.MonkeyPatch):
    results = [[{"field": "@timestamp", "value": "t1"}]]
    fake_client = _FakeInsightsClient(results, bytes_scanned=10)
    monkeypatch.setattr(cw_utils, "get_logs_client", lambda settings: fake_client)
    start, end = _window()

    result = cw_get_trace_events(LOG_GROUP, "trace_id", 'has "quote', start, end)

    assert result["ok"] is True
    assert result["data"]["field"] == "trace_id"
    assert result["data"]["value"] == 'has "quote'
    assert 'filter trace_id = "has \\"quote"' in result["data"]["executed_query"]
    assert result["data"]["rows"] == [{"@timestamp": "t1"}]
