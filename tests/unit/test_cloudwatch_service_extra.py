"""CloudWatchService methods not covered in test_cloudwatch_tools.py:
list_log_groups, filter_events, get_metric_stats and AWS error handling."""

from datetime import UTC, datetime, timedelta

import pytest
from botocore.exceptions import ClientError

from opsinsight.core.config import get_settings
from opsinsight.service.cloudwatch_service import CloudWatchService

ALLOWED = "/aws/ecs/allowed"


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDWATCH_ALLOWED_LOG_GROUP", ALLOWED)
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _client_error(code: str = "AccessDeniedException") -> ClientError:
    return ClientError({"Error": {"Code": code, "Message": "nope"}}, "Op")


class _Paginator:
    def __init__(self, pages):
        self.pages = pages
        self.kwargs = None

    def paginate(self, **kwargs):
        self.kwargs = kwargs
        return iter(self.pages)


class _LogsClient:
    def __init__(self, pages=None, events=None, exc=None):
        self.paginator = _Paginator(pages or [])
        self.events = events or []
        self.exc = exc
        self.filter_kwargs = None

    def get_paginator(self, name):
        assert name == "describe_log_groups"
        if self.exc:
            raise self.exc
        return self.paginator

    def filter_log_events(self, **kwargs):
        if self.exc:
            raise self.exc
        self.filter_kwargs = kwargs
        return {"events": self.events}


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


def _cw(logs=None, metrics=None) -> CloudWatchService:
    return CloudWatchService(
        get_settings(),
        logs_client_factory=lambda s: logs,
        metrics_client_factory=lambda s: metrics,
    )


def test_list_log_groups_only_returns_allowlisted_groups():
    logs = _LogsClient(
        pages=[
            {"logGroups": [{"logGroupName": ALLOWED, "retentionInDays": 7, "storedBytes": 10}]},
            {"logGroups": [{"logGroupName": "/aws/secret"}]},
        ]
    )

    result = _cw(logs).list_log_groups(prefix="/aws")

    assert result["ok"] is True
    assert result["data"]["log_groups"] == [
        {"log_group": ALLOWED, "retention_days": 7, "stored_bytes": 10}
    ]
    assert logs.paginator.kwargs == {"logGroupNamePrefix": "/aws"}


def test_list_log_groups_fails_closed_without_allowlist(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDWATCH_ALLOWED_LOG_GROUP", "")
    get_settings.cache_clear()

    result = _cw(_LogsClient(exc=AssertionError("must not call AWS"))).list_log_groups()

    assert result["data"]["log_groups"] == []
    assert "not set" in result["data"]["note"]


def test_list_log_groups_reports_aws_errors():
    result = _cw(_LogsClient(exc=_client_error())).list_log_groups()
    assert result["ok"] is False


def test_filter_events_redacts_messages_and_bounds_the_window():
    logs = _LogsClient(
        events=[{"timestamp": 1, "message": "user jane@example.com failed", "logStreamName": "s1"}]
    )

    result = _cw(logs).filter_events(ALLOWED, "ERROR", minutes=15)

    assert result["ok"] is True
    [event] = result["data"]["events"]
    assert "jane@example.com" not in event["message"]
    assert event["log_stream"] == "s1"
    assert logs.filter_kwargs["filterPattern"] == "ERROR"
    span_ms = logs.filter_kwargs["endTime"] - logs.filter_kwargs["startTime"]
    assert span_ms == 15 * 60 * 1000


def test_filter_events_rejects_unlisted_log_group():
    result = _cw(_LogsClient()).filter_events("/aws/secret", "ERROR")
    assert result["error"]["rule"] == "log_group_not_allowed"


def test_filter_events_reports_aws_errors():
    result = _cw(_LogsClient(exc=_client_error())).filter_events(ALLOWED, "ERROR")
    assert result["ok"] is False


def test_get_metric_stats_sorts_datapoints_and_serializes_timestamps():
    t = datetime(2026, 10, 1, tzinfo=UTC)
    metrics = _MetricsClient(
        datapoints=[{"Timestamp": t + timedelta(minutes=5), "Average": 2.0}, {"Timestamp": t, "Average": 1.0}]
    )
    end = datetime.now(UTC)

    result = _cw(metrics=metrics).get_metric_stats(
        "AWS/ECS", "CPUUtilization", {"ServiceName": "api"}, 60,
        (end - timedelta(hours=1)).isoformat(), end.isoformat(),
    )

    assert result["ok"] is True
    assert [d["Average"] for d in result["data"]["datapoints"]] == [1.0, 2.0]
    assert result["data"]["datapoints"][0]["Timestamp"] == t.isoformat()
    assert metrics.kwargs["Dimensions"] == [{"Name": "ServiceName", "Value": "api"}]


def test_get_metric_stats_rejects_oversized_window():
    end = datetime.now(UTC)
    result = _cw(metrics=_MetricsClient()).get_metric_stats(
        "AWS/ECS", "CPU", {}, 60, (end - timedelta(days=30)).isoformat(), end.isoformat()
    )
    assert result["error"]["rule"] == "window_too_large"


def test_get_metric_stats_reports_aws_errors():
    end = datetime.now(UTC)
    result = _cw(metrics=_MetricsClient(exc=_client_error())).get_metric_stats(
        "AWS/ECS", "CPU", {}, 60, (end - timedelta(hours=1)).isoformat(), end.isoformat()
    )
    assert result["ok"] is False
