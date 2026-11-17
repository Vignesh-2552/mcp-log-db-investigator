"""CloudWatchMetricsService.get_metric_stats."""

from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from opsinsight.core.config import get_settings
from opsinsight.service.cloudwatch_metrics_service import CloudWatchMetricsService

ALLOWED = "/aws/ecs/allowed"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDWATCH_ALLOWED_LOG_GROUP", ALLOWED)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client_error(code: str = "AccessDeniedException") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "nope"}}, "Op")


class _MetricsClient:
    def __init__(self, datapoints=None, exc=None):
        self.datapoints = datapoints or []
        self.exc = exc
        self.kwargs = None

    def get_metric_statistics(self, **kwargs):
        if self.exc:
            raise self.exc
        self.kwargs = kwargs
        return {"Datapoints": self.datapoints}


def _metrics(metrics=None) -> CloudWatchMetricsService:
    return CloudWatchMetricsService(get_settings(), metrics_client_factory=lambda s: metrics)


def test_get_metric_stats_sorts_datapoints_and_serializes_timestamps():
    t = datetime(2026, 10, 1, tzinfo=UTC)
    metrics = _MetricsClient(
        datapoints=[{"Timestamp": t + timedelta(minutes=5), "Average": 2.0}, {"Timestamp": t, "Average": 1.0}]
    )
    end = datetime.now(UTC)

    result = _metrics(metrics).get_metric_stats(
        "AWS/ECS", "CPUUtilization", {"ServiceName": "api"}, 60,
        (end - timedelta(hours=1)).isoformat(), end.isoformat(),
    )

    assert result["ok"] is True
    assert [d["Average"] for d in result["data"]["datapoints"]] == [1.0, 2.0]
    assert result["data"]["datapoints"][0]["Timestamp"] == t.isoformat()
    assert metrics.kwargs["Dimensions"] == [{"Name": "ServiceName", "Value": "api"}]


def test_get_metric_stats_rejects_oversized_window():
    end = datetime.now(UTC)
    result = _metrics(_MetricsClient()).get_metric_stats(
        "AWS/ECS", "CPU", {}, 60, (end - timedelta(days=30)).isoformat(), end.isoformat()
    )
    assert result["error"]["rule"] == "window_too_large"


def test_get_metric_stats_reports_aws_errors():
    end = datetime.now(UTC)
    result = _metrics(_MetricsClient(exc=_client_error())).get_metric_stats(
        "AWS/ECS", "CPU", {}, 60, (end - timedelta(hours=1)).isoformat(), end.isoformat()
    )
    assert result["ok"] is False
