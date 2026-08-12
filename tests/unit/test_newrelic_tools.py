import pytest

from core.config import get_settings
from tools import newrelic_tools


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEW_RELIC_API_KEY", "test-key")
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "123")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


async def test_list_event_types_returns_sorted_unique_names(monkeypatch: pytest.MonkeyPatch):
    async def fake_run_nrql(query, settings):
        assert query.startswith("SHOW EVENT TYPES")
        return {
            "results": [{"eventType": "Transaction"}, {"eventType": "Log"}, {"eventType": "Log"}],
            "metadata": {},
        }

    monkeypatch.setattr(newrelic_tools, "run_nrql", fake_run_nrql)

    result = await newrelic_tools.nr_list_event_types()

    assert result["ok"] is True
    assert result["data"]["event_types"] == ["Log", "Transaction"]
    assert result["data"]["note"] is None
    assert result["meta"]["event_type_count"] == 2


async def test_list_event_types_notes_empty_result(monkeypatch: pytest.MonkeyPatch):
    async def fake_run_nrql(query, settings):
        return {"results": [], "metadata": {}}

    monkeypatch.setattr(newrelic_tools, "run_nrql", fake_run_nrql)

    result = await newrelic_tools.nr_list_event_types(hours=6)

    assert result["ok"] is True
    assert result["data"]["event_types"] == []
    assert result["data"]["note"] is not None


async def test_describe_log_fields_rejects_invalid_event_type():
    result = await newrelic_tools.nr_describe_log_fields(event_type="Log; DROP")
    assert result["ok"] is False
    assert result["error"]["rule"] == "invalid_event_type"


async def test_describe_log_fields_returns_keyset_and_correlation_candidates(
    monkeypatch: pytest.MonkeyPatch,
):
    async def fake_run_nrql(query, settings):
        assert "keyset()" in query
        assert "FROM Log" in query
        return {
            "results": [{"keyset": ["message", "level", "trace.id", "span.id", "service"]}],
            "metadata": {},
        }

    monkeypatch.setattr(newrelic_tools, "run_nrql", fake_run_nrql)

    result = await newrelic_tools.nr_describe_log_fields()

    assert result["ok"] is True
    assert result["data"]["fields"] == ["level", "message", "service", "span.id", "trace.id"]
    assert result["data"]["correlation_id_candidates"] == ["span.id", "trace.id"]
    assert result["data"]["note"] is None


async def test_describe_log_fields_notes_empty_result(monkeypatch: pytest.MonkeyPatch):
    async def fake_run_nrql(query, settings):
        return {"results": [], "metadata": {}}

    monkeypatch.setattr(newrelic_tools, "run_nrql", fake_run_nrql)

    result = await newrelic_tools.nr_describe_log_fields(event_type="Transaction", hours=6)

    assert result["ok"] is True
    assert result["data"]["fields"] == []
    assert result["data"]["note"] is not None
