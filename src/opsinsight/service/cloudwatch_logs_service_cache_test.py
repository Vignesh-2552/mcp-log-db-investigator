"""CloudWatch Insights result caching: only closed windows, only Complete
results, and a hit reports no new bytes scanned."""

from datetime import UTC, datetime, timedelta

import pytest

from opsinsight.core.config import get_settings
from opsinsight.core.response_cache import MemoryBackend, ResponseCache
from opsinsight.service.cloudwatch_logs_service import CloudWatchLogsService

LOG_GROUP = "/aws/ecs/test-svc"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDWATCH_ALLOWED_LOG_GROUP", LOG_GROUP)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _InsightsClient:
    def __init__(self, status="Complete"):
        self.status = status
        self.started = 0

    def start_query(self, **kwargs):
        self.started += 1
        return {"queryId": f"q-{self.started}"}

    def get_query_results(self, queryId):
        return {
            "status": self.status,
            "results": [[{"field": "@message", "value": "boom"}]],
            "statistics": {"bytesScanned": 1234, "recordsMatched": 1},
        }

    def stop_query(self, queryId):
        pass

    def get_paginator(self, name):
        client = self

        class _P:
            def paginate(self, **kwargs):
                client.started += 1
                return [{"logGroups": [{"logGroupName": LOG_GROUP}]}]

        return _P()


def _service(client) -> CloudWatchLogsService:
    return CloudWatchLogsService(
        get_settings(), logs_client_factory=lambda s: client, cache=ResponseCache(MemoryBackend())
    )


def _window(ended_ago: timedelta) -> tuple[str, str]:
    end = datetime.now(UTC) - ended_ago
    return (end - timedelta(hours=1)).isoformat(), end.isoformat()


def test_closed_window_result_is_served_from_cache():
    client = _InsightsClient()
    service = _service(client)
    start, end = _window(timedelta(hours=2))

    first = service.run_insights_query([LOG_GROUP], "fields @message", start, end, 10, False)
    second = service.run_insights_query([LOG_GROUP], "fields @message", start, end, 10, False)

    assert client.started == 1
    assert first["meta"]["cache"] == "miss"
    assert second["meta"]["cache"] == "hit"
    assert second["meta"]["bytes_scanned"] == 0  # nothing scanned or billed for the repeat
    assert second["data"]["rows"] == first["data"]["rows"]


def test_open_window_is_never_cached():
    client = _InsightsClient()
    service = _service(client)
    start, end = _window(timedelta(minutes=1))  # inside CW_RESULT_CACHE_SETTLE_S

    service.run_insights_query([LOG_GROUP], "fields @message", start, end, 10, False)
    result = service.run_insights_query([LOG_GROUP], "fields @message", start, end, 10, False)

    assert client.started == 2
    assert "cache" not in result["meta"]


def test_incomplete_results_are_not_cached(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CW_POLL_MAX_WAIT_S", "0")
    get_settings.cache_clear()
    client = _InsightsClient(status="Running")
    service = _service(client)
    start, end = _window(timedelta(hours=2))

    for _ in range(2):
        result = service.run_insights_query([LOG_GROUP], "fields @message", start, end, 10, False)

    assert result["data"]["status"] == "Stopped"
    assert client.started == 2


def test_trace_events_reuse_the_cached_insights_result():
    client = _InsightsClient()
    service = _service(client)
    start, end = _window(timedelta(hours=2))

    service.get_trace_events(LOG_GROUP, "requestId", "R-1", start, end)
    second = service.get_trace_events(LOG_GROUP, "requestId", "R-1", start, end)

    assert client.started == 1
    assert second["data"]["value"] == "R-1"


def test_log_group_inventory_is_cached():
    client = _InsightsClient()
    service = _service(client)

    service.list_log_groups()
    result = service.list_log_groups()

    assert client.started == 1
    assert result["meta"]["cache"] == "hit"
