import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from opsinsight.core.config import get_settings
from opsinsight.core.errors import NewRelicGuardrailError
from opsinsight.integrations.newrelic.guardrail import (
    build_nrql_string_literal,
    validate_attribute_name,
    validate_nrql,
)
from opsinsight.service.cloudwatch_service import CloudWatchService
from opsinsight.service.newrelic_service import NewRelicService
from opsinsight.service.trace_service import TraceService

LOG_GROUP = "/aws/ecs/test-svc"
NOW = datetime.now(UTC).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    return dt.isoformat()


def _cw_time(dt: datetime) -> str:
    # Logs Insights' @timestamp format.
    return dt.strftime("%Y-%m-%d %H:%M:%S.000")


@pytest.fixture(autouse=True)
def _settings_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("CLOUDWATCH_ALLOWED_LOG_GROUP", f"{LOG_GROUP},/aws/ecs/other")
    monkeypatch.setenv("NEW_RELIC_API_KEY", "test-key")
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "123")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeInsightsClient:
    def __init__(self, rows: list[dict], bytes_scanned: int = 100, status: str = "Complete"):
        self.results = [[{"field": k, "value": v} for k, v in row.items()] for row in rows]
        self.bytes_scanned = bytes_scanned
        self.status = status
        self.started: dict | None = None

    def start_query(self, **kwargs):
        self.started = kwargs
        return {"queryId": "q-1"}

    def get_query_results(self, queryId):
        return {
            "status": self.status,
            "results": self.results,
            "statistics": {"bytesScanned": self.bytes_scanned},
        }

    def stop_query(self, queryId):
        pass


class _FakeDbService:
    def __init__(self, response: dict | None = None, delay: float = 0, exc: Exception | None = None):
        self.response = response
        self.delay = delay
        self.exc = exc
        self.calls: list[tuple[str, str]] = []

    async def search_by_identifier(self, identifier: str, id_type: str) -> dict:
        self.calls.append((identifier, id_type))
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.exc:
            raise self.exc
        return self.response


def _db_ok(matches: list[dict], note: str | None = None) -> dict:
    return {
        "ok": True,
        "data": {"matches": matches, "data_freshness_note": note},
        "meta": {"row_count": sum(len(m["rows"]) for m in matches), "truncated": False},
    }


def _build(
    cw_rows: list[dict] | None = None,
    nr_rows: list[dict] | None = None,
    db: _FakeDbService | None = None,
    nr_fn=None,
    cw_client=None,
) -> tuple[TraceService, _FakeInsightsClient, list[str]]:
    settings = get_settings()
    cw_client = cw_client or _FakeInsightsClient(cw_rows or [])
    nr_queries: list[str] = []

    async def fake_run_nrql(query, _settings):
        nr_queries.append(query)
        return {"results": nr_rows or [], "metadata": {}}

    service = TraceService(
        settings,
        database_service=db or _FakeDbService(_db_ok([])),
        cloudwatch_service=CloudWatchService(settings, logs_client_factory=lambda s: cw_client),
        newrelic_service=NewRelicService(settings, run_nrql_fn=nr_fn or fake_run_nrql),
    )
    return service, cw_client, nr_queries


def _window() -> tuple[str, str]:
    return _iso(NOW - timedelta(hours=1)), _iso(NOW)


async def test_merges_all_three_sources_into_one_sorted_timeline():
    t = NOW - timedelta(minutes=30)
    db = _FakeDbService(
        _db_ok([{"table": "public.orders", "column": "order_id",
                 "rows": [{"order_id": "R-1", "created_at": t}]}])
    )
    service, _, nr_queries = _build(
        cw_rows=[{"@timestamp": _cw_time(t + timedelta(seconds=20)), "@message": "payment failed"}],
        nr_rows=[{"timestamp": int((t + timedelta(seconds=10)).timestamp() * 1000), "message": "POST /pay"}],
        db=db,
    )
    start, end = _window()

    result = await service.trace_request("R-1", start, end, db_id_type="order_id")

    assert result["ok"] is True
    timeline = result["data"]["timeline"]
    assert [e["source"] for e in timeline] == ["db", "newrelic", "cloudwatch"]
    assert [e["summary"] for e in timeline][1:] == ["POST /pay", "payment failed"]
    assert timeline[0]["origin"] == "public.orders"
    assert timeline[1]["timestamp"] == _iso(t + timedelta(seconds=10))
    assert all(e["in_window"] for e in timeline)
    assert result["meta"]["event_count"] == 3
    assert result["meta"]["bytes_scanned"] == 100
    sources = result["data"]["sources"]
    assert {k: v["status"] for k, v in sources.items()} == {"cloudwatch": "ok", "newrelic": "ok", "db": "ok"}
    assert 'filter requestId = "R-1"' in sources["cloudwatch"]["executed_query"]
    assert "WHERE `requestId` = 'R-1'" in nr_queries[0]
    assert db.calls == [("R-1", "order_id")]


async def test_cloudwatch_searches_all_log_groups_in_one_query():
    service, cw_client, _ = _build()
    start, end = _window()

    await service.trace_request("R-1", start, end, sources=["cloudwatch"])

    assert sorted(cw_client.started["logGroupNames"]) == ["/aws/ecs/other", LOG_GROUP]


async def test_db_rows_outside_window_or_without_time_are_flagged():
    old = NOW - timedelta(days=30)
    db = _FakeDbService(
        _db_ok(
            [{"table": "public.orders", "column": "order_id",
              "rows": [{"order_id": "R-1", "updated_at": old}, {"order_id": "R-1", "status": "paid"}]}],
            note="matches only in historical schemas",
        )
    )
    service, _, _ = _build(db=db)
    start, end = _window()

    result = await service.trace_request("R-1", start, end, sources=["db"], db_id_type="order_id")

    assert result["ok"] is True
    assert len(result["data"]["timeline"]) == 1
    assert result["data"]["timeline"][0]["in_window"] is False
    assert len(result["data"]["undated"]) == 1
    assert result["data"]["undated"][0]["in_window"] is None
    assert any("not filtered by the time window" in n for n in result["data"]["notes"])
    assert "matches only in historical schemas" in result["data"]["notes"]


async def test_one_failing_source_does_not_fail_the_call():
    service, _, _ = _build(
        cw_rows=[{"@timestamp": _cw_time(NOW - timedelta(minutes=5)), "@message": "hi"}],
        db=_FakeDbService(exc=RuntimeError("boom")),
    )
    start, end = _window()

    result = await service.trace_request("R-1", start, end, db_id_type="order_id")

    assert result["ok"] is True
    sources = result["data"]["sources"]
    assert sources["db"]["status"] == "error"
    assert sources["db"]["error"]["rule"] == "source_failed"
    assert "boom" not in sources["db"]["error"]["message"]  # no raw exception text
    assert sources["cloudwatch"]["status"] == "ok"
    assert len(result["data"]["timeline"]) == 1


async def test_slow_source_is_reported_as_timeout(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TRACE_SOURCE_TIMEOUT_S", "0.05")
    get_settings.cache_clear()
    service, _, _ = _build(db=_FakeDbService(_db_ok([]), delay=1))
    start, end = _window()

    result = await service.trace_request("R-1", start, end, db_id_type="order_id")

    assert result["ok"] is True
    assert result["data"]["sources"]["db"]["status"] == "timeout"
    assert result["data"]["sources"]["db"]["error"]["rule"] == "source_timeout"


async def test_guardrail_error_from_a_source_is_passed_through():
    service, _, _ = _build()
    start, end = _window()

    result = await service.trace_request("R-1", start, end, nr_field="bad field!")

    assert result["ok"] is True
    assert result["data"]["sources"]["newrelic"]["status"] == "error"
    assert result["data"]["sources"]["newrelic"]["error"]["rule"] == "invalid_field_name"


async def test_all_sources_failing_returns_error():
    async def failing_nrql(query, settings):
        raise RuntimeError("down")

    service, _, _ = _build(nr_fn=failing_nrql, db=_FakeDbService(exc=RuntimeError("down")))
    start, end = _window()

    result = await service.trace_request(
        "R-1", start, end, sources=["newrelic", "db"], db_id_type="order_id"
    )

    assert result["ok"] is False
    assert result["error"]["rule"] == "all_sources_failed"
    assert result["meta"]["sources"]["cloudwatch"]["status"] == "skipped"


async def test_unconfigured_sources_are_skipped(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("NEW_RELIC_ACCOUNT_ID", "")  # beats any value in a local .env
    get_settings.cache_clear()
    service, _, _ = _build()
    start, end = _window()

    result = await service.trace_request("R-1", start, end)

    assert result["ok"] is True
    sources = result["data"]["sources"]
    assert sources["newrelic"]["status"] == "skipped"
    assert sources["db"]["status"] == "skipped"
    assert "db_id_type" in sources["db"]["reason"]
    assert sources["cloudwatch"]["status"] == "ok"


async def test_nothing_searchable_returns_error():
    service, _, _ = _build()
    start, end = _window()

    result = await service.trace_request("R-1", start, end, sources=["db"])

    assert result["ok"] is False
    assert result["error"]["rule"] == "no_sources_available"


async def test_limit_truncates_earliest_first():
    rows = [
        {"@timestamp": _cw_time(NOW - timedelta(minutes=m)), "@message": f"m{m}"} for m in (1, 2, 3, 4)
    ]
    service, _, _ = _build(cw_rows=rows)
    start, end = _window()

    result = await service.trace_request("R-1", start, end, sources=["cloudwatch"], limit=2)

    assert result["meta"]["truncated"] is True
    assert [e["summary"] for e in result["data"]["timeline"]] == ["m4", "m3"]
    assert any("Returned 2 of 4" in n for n in result["data"]["notes"])


@pytest.mark.parametrize(
    ("kwargs", "rule"),
    [
        ({"value": "  "}, "empty_value"),
        ({"value": "R-1", "sources": ["splunk"]}, "unknown_source"),
        ({"value": "R-1", "start": _iso(NOW - timedelta(days=30)), "end": _iso(NOW)}, "window_too_large"),
    ],
)
async def test_invalid_input_is_rejected(kwargs, rule):
    service, _, _ = _build()
    result = await service.trace_request(**kwargs)
    assert result["ok"] is False
    assert result["error"]["rule"] == rule


# New Relic trace query building


async def test_nr_get_trace_events_escapes_value_and_bounds_window():
    queries: list[str] = []

    async def fake_run_nrql(query, settings):
        queries.append(query)
        return {"results": [], "metadata": {}}

    service = NewRelicService(get_settings(), run_nrql_fn=fake_run_nrql)
    since, until = NOW - timedelta(hours=1), NOW

    result = await service.get_trace_events("Log", "trace.id", "it's", since, until, limit=50)

    assert result["ok"] is True
    assert "`trace.id` = 'it\\'s'" in queries[0]
    assert f"SINCE {int(since.timestamp() * 1000)} UNTIL {int(until.timestamp() * 1000)}" in queries[0]
    assert queries[0].endswith("LIMIT 50")


async def test_nr_get_trace_events_rejects_bad_event_type():
    service = NewRelicService(get_settings())
    result = await service.get_trace_events("Log; x", "f", "v", NOW - timedelta(hours=1), NOW)
    assert result["error"]["rule"] == "invalid_event_type"


def test_nrql_literal_cannot_break_out():
    literal = build_nrql_string_literal("x' OR 1=1 SINCE 1 year ago --\\")
    query = f"SELECT * FROM Log WHERE `f` = '{literal}' SINCE 1 hours ago"
    # The whole value stays inside one literal, so the window check still sees the real SINCE.
    assert validate_nrql(query, 100).endswith("LIMIT 100")


def test_nrql_literal_rejects_newlines():
    with pytest.raises(NewRelicGuardrailError) as exc:
        build_nrql_string_literal("a\nSINCE 1 year ago")
    assert exc.value.rule == "invalid_trace_value"


@pytest.mark.parametrize("name", ["a b", "a`b", "1abc", "", "a;b"])
def test_attribute_name_rejects_unsafe_names(name):
    with pytest.raises(NewRelicGuardrailError):
        validate_attribute_name(name)
