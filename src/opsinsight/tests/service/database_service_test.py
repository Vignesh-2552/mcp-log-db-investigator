"""DatabaseService without a real Postgres: introspection helpers and the
engine are replaced with fakes, so these cover the service's own logic
(error translation, redaction, capping, response shape). Real-database
behaviour is covered by database_service_integration_test.py."""

from contextlib import asynccontextmanager

import pytest
from sqlalchemy.exc import OperationalError

from opsinsight.core.config import Settings
from opsinsight.integrations.database import introspect
from opsinsight.integrations.database.models import (
    SampleRowsResult,
    StoreCandidate,
    StoreResolutionResult,
)
from opsinsight.service.database_service import DatabaseService


class _Result:
    def __init__(self, columns, rows, scalar=None):
        self._columns, self._rows, self._scalar = columns, rows, scalar

    def keys(self):
        return self._columns

    def fetchall(self):
        return self._rows

    def scalar(self):
        return self._scalar


class _FakeEngine:
    def __init__(self, result=None, exc=None):
        self.result, self.exc = result, exc
        self.executed: list[str] = []

    @asynccontextmanager
    async def connect(self):
        engine = self

        class _Conn:
            async def execute(self, statement):
                engine.executed.append(str(statement))
                if engine.exc:
                    raise engine.exc
                return engine.result

        yield _Conn()


def _db_error(message: str) -> OperationalError:
    return OperationalError("SELECT ...", {}, Exception(message))


def _service(engine: "_FakeEngine | None" = None, **overrides) -> DatabaseService:
    if engine is None:
        return DatabaseService(Settings(**overrides))
    return DatabaseService(Settings(**overrides), engine_factory=lambda settings: engine)


@pytest.fixture
def engine():
    return _FakeEngine()


async def test_run_query_returns_redacted_rows_and_executed_sql(engine):
    engine.result = _Result(["id", "email"], [(1, "jane@example.com")])

    result = await _service(engine).run_query("SELECT id, email FROM public.users", limit=10)

    assert result["ok"] is True
    data = result["data"]
    assert data["columns"] == ["id", "email"]
    assert data["rows"][0]["id"] == 1
    assert "jane@example.com" not in str(data["rows"])
    assert "LIMIT 10" in data["executed_sql"].upper()
    assert result["meta"] == {"row_count": 1, "truncated": False}


async def test_run_query_truncates_to_response_budget(engine):
    engine.result = _Result(["note"], [("x" * 200,) for _ in range(50)])

    result = await _service(engine, db_max_response_bytes=1000).run_query("SELECT note FROM public.t")

    assert result["ok"] is True
    assert 0 < result["meta"]["row_count"] < 50
    assert result["meta"]["truncated"] is True
    assert "of 50 fetched rows" in result["meta"]["truncation_note"]


async def test_run_query_rejects_writes_before_touching_the_db(engine):
    result = await _service(engine).run_query("DELETE FROM public.users")

    assert result["ok"] is False
    assert engine.executed == []


async def test_statement_timeout_is_reported_as_query_timeout(engine):
    engine.exc = _db_error("canceling statement due to statement timeout")

    result = await _service(engine).run_query("SELECT 1")

    assert result["error"]["rule"] == "query_timeout"
    assert "db_explain_query" in result["error"]["detail"]


async def test_other_db_errors_are_reported_with_driver_detail(engine):
    engine.exc = _db_error('relation "nope" does not exist')

    result = await _service(engine).run_query("SELECT * FROM nope")

    assert result["error"]["rule"] == "query_execution_error"
    assert 'relation "nope" does not exist' in result["error"]["detail"]


async def test_explain_query_returns_plan(engine):
    engine.result = _Result([], [], scalar=[{"Plan": {"Node Type": "Seq Scan"}}])

    result = await _service(engine).explain_query("SELECT * FROM public.orders")

    assert result["ok"] is True
    assert result["data"]["plan"] == [{"Plan": {"Node Type": "Seq Scan"}}]
    assert result["data"]["executed_sql"].upper().startswith("EXPLAIN")


async def test_explain_query_reports_db_errors(engine):
    engine.exc = _db_error("boom")
    result = await _service(engine).explain_query("SELECT 1")
    assert result["error"]["rule"] == "query_execution_error"


async def test_explain_query_rejects_non_select(engine):
    result = await _service(engine).explain_query("DROP TABLE x")
    assert result["ok"] is False
    assert engine.executed == []


class _Dictable:
    def __init__(self, **data):
        self.__dict__.update(data)

    def to_dict(self):
        return dict(self.__dict__)


async def test_list_tables_wraps_introspection(monkeypatch: pytest.MonkeyPatch):
    async def fake_list_tables(schema, settings):
        assert schema == "public"
        return [_Dictable(table="public.orders"), _Dictable(table="public.users")]

    monkeypatch.setattr(introspect, "list_tables", fake_list_tables)

    result = await _service().list_tables("public")

    assert result["data"]["tables"] == [{"table": "public.orders"}, {"table": "public.users"}]
    assert result["meta"] == {"row_count": 2}


async def test_describe_table_wraps_introspection(monkeypatch: pytest.MonkeyPatch):
    async def fake_describe(table, settings):
        return _Dictable(table=table, columns=[])

    monkeypatch.setattr(introspect, "describe_table", fake_describe)

    result = await _service().describe_table("public.orders")

    assert result["data"] == {"table": "public.orders", "columns": []}


async def test_sample_rows_caps_rows(monkeypatch: pytest.MonkeyPatch):
    async def fake_sample(table, limit, settings):
        rows = [{"note": "y" * 300} for _ in range(10)]
        return SampleRowsResult(table=table, columns=["note"], rows=rows, row_count=len(rows))

    monkeypatch.setattr(introspect, "sample_rows", fake_sample)

    result = await _service(db_max_response_bytes=1000).sample_rows("public.t", 10)

    assert result["meta"]["truncated"] is True
    assert result["data"]["row_count"] == result["meta"]["row_count"] < 10


async def test_resolve_store_reports_candidate_count(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve(name, settings):
        return _store_result([StoreCandidate("public.stores", "domain", i, {"id": i}) for i in (1, 2)])

    monkeypatch.setattr(introspect, "resolve_store", fake_resolve)

    result = await _service().resolve_store("shop")

    assert result["meta"] == {"candidate_count": 2, "row_count": 2, "truncated": False}
    assert result["data"]["ambiguous"] is True


async def test_resolve_store_caps_candidate_rows_to_response_budget(monkeypatch: pytest.MonkeyPatch):
    async def fake_resolve(name, settings):
        rows = [StoreCandidate("public.stores", "domain", i, {"id": i, "note": "x" * 200}) for i in range(50)]
        return _store_result(rows)

    monkeypatch.setattr(introspect, "resolve_store", fake_resolve)

    result = await _service(db_max_response_bytes=1000).resolve_store("shop")

    assert result["meta"]["candidate_count"] == 50
    assert 0 < len(result["data"]["candidates"]) == result["meta"]["row_count"] < 50
    assert result["meta"]["truncated"] is True
    assert result["data"]["ambiguous"] is True  # decided on all 50, not the shown rows


def _store_result(candidates):
    return StoreResolutionResult(
        name_or_domain="shop",
        searched_columns=["public.stores.domain"],
        truncated=False,
        ambiguous=len({c.store_id for c in candidates}) > 1,
        store_id=None,
        candidates=candidates,
        skipped=[],
        note=None,
    )


async def test_run_query_keeps_both_duplicate_columns(engine):
    engine.result = _Result(["id", "id", "name"], [(1, 2, "x")])

    result = await _service(engine).run_query("SELECT o.id, u.id, u.name FROM o JOIN u ON true")

    assert result["data"]["columns"] == ["id", "id_2", "name"]
    assert result["data"]["rows"] == [{"id": 1, "id_2": 2, "name": "x"}]


async def test_run_query_returns_binary_cells_as_hex(engine):
    engine.result = _Result(["blob", "text_blob"], [(b"\xff\x00", b"hello")])

    result = await _service(engine).run_query("SELECT blob, text_blob FROM t")

    assert result["data"]["rows"] == [{"blob": "\\xff00", "text_blob": "hello"}]


async def test_db_error_detail_is_redacted(engine):
    engine.exc = _db_error('invalid input syntax for type integer: "jane@example.com"')

    result = await _service(engine).run_query("SELECT email::int FROM users")

    assert "jane@example.com" not in result["error"]["detail"]
    assert "[REDACTED_EMAIL]" in result["error"]["detail"]


@pytest.mark.parametrize(
    ("method", "args", "target"),
    [
        ("list_tables", (None,), "list_tables"),
        ("describe_table", ("t",), "describe_table"),
        ("sample_rows", ("t",), "sample_rows"),
        ("search_by_identifier", ("1", "order_id"), "search_by_identifier"),
        ("resolve_store", ("shop",), "resolve_store"),
    ],
)
async def test_introspection_errors_become_structured_responses(
    monkeypatch: pytest.MonkeyPatch, method, args, target
):
    async def raises_db_error(*_a, **_k):
        raise _db_error("connection refused")

    monkeypatch.setattr(introspect, target, raises_db_error)
    result = await getattr(_service(), method)(*args)
    assert result == {
        "ok": False,
        "error": {
            "rule": "query_execution_error",
            "message": "Query failed to execute.",
            "detail": "connection refused",
            "allowed": None,
        },
    }


async def test_introspection_tool_errors_pass_through(monkeypatch: pytest.MonkeyPatch):
    from opsinsight.core.errors import GuardrailError

    async def raises_tool_error(*_a, **_k):
        raise GuardrailError(rule="table_not_found", message="no such table")

    monkeypatch.setattr(introspect, "describe_table", raises_tool_error)
    result = await _service().describe_table("nope")
    assert result["error"]["rule"] == "table_not_found"


async def test_catalog_is_constructor_injected():
    class _FakeCatalog:
        async def list_tables(self, schema, settings=None):
            from opsinsight.integrations.database.models import TableSummary

            return [TableSummary(table="public.orders", row_estimate=3, comment=None)]

    service = DatabaseService(Settings(), catalog=_FakeCatalog())
    result = await service.list_tables("public")
    assert result["ok"] is True
    assert result["data"]["tables"][0]["table"] == "public.orders"


async def test_catalog_metadata_is_cached_but_queries_are_not(engine):
    from opsinsight.core.response_cache import MemoryBackend, ResponseCache
    from opsinsight.integrations.database.models import TableSummary

    calls = []

    class _Catalog:
        async def list_tables(self, schema, settings=None):
            calls.append(schema)
            return [TableSummary(table="public.t", row_estimate=1, comment=None)]

    engine.result = _Result(["n"], [(1,)])
    service = DatabaseService(
        Settings(), engine_factory=lambda s: engine, catalog=_Catalog(), cache=ResponseCache(MemoryBackend())
    )

    await service.list_tables("public")
    second = await service.list_tables("public")
    await service.run_query("SELECT 1 AS n")
    await service.run_query("SELECT 1 AS n")

    assert calls == ["public"]
    assert second["meta"]["cache"] == "hit"
    assert len(engine.executed) == 2  # live data always re-queried
