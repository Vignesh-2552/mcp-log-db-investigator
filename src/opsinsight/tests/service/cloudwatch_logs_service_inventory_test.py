"""CloudWatchLogsService: list_log_groups, filter_events and AWS error handling."""


import pytest
from botocore.exceptions import ClientError

from opsinsight.core.config import get_settings
from opsinsight.service.cloudwatch_logs_service import CloudWatchLogsService

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


def _cw(logs=None) -> CloudWatchLogsService:
    return CloudWatchLogsService(get_settings(), logs_client_factory=lambda s: logs)


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


