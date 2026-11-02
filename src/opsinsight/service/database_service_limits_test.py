"""DatabaseService load protection: response byte budget, concurrent query
slots and bounded search fan-out."""

import asyncio
import json

import pytest

from opsinsight.core.config import Settings
from opsinsight.integrations.database import introspect
from opsinsight.integrations.database.models import (
    IdentifierMatch,
    IdentifierSearchResult,
    SampleRowsResult,
)
from opsinsight.service.database_service import DatabaseService


def _row_bytes(row: dict) -> int:
    return len(json.dumps(row, default=str).encode("utf-8")) + 1


async def test_sample_rows_response_is_capped_and_flagged(monkeypatch: pytest.MonkeyPatch):
    rows = [{"id": i, "payload": "y" * 200} for i in range(50)]

    async def fake_sample_rows(table, limit, settings):
        return SampleRowsResult(table="public.t", columns=["id", "payload"], rows=list(rows), row_count=len(rows))

    monkeypatch.setattr(introspect, "sample_rows", fake_sample_rows)
    service = DatabaseService(Settings(db_max_response_bytes=2_000))

    response = await service.sample_rows("t", 50)

    assert response["ok"] is True
    returned = response["data"]["rows"]
    assert 0 < len(returned) < 50
    assert response["data"]["row_count"] == len(returned)
    assert response["meta"]["truncated"] is True
    assert f"returned {len(returned)} of 50" in response["meta"]["truncation_note"]
    assert sum(_row_bytes(r) for r in returned) <= 2_000


async def test_sample_rows_truncates_oversized_cells(monkeypatch: pytest.MonkeyPatch):
    async def fake_sample_rows(table, limit, settings):
        return SampleRowsResult(table="public.t", columns=["doc"], rows=[{"doc": "z" * 10_000}], row_count=1)

    monkeypatch.setattr(introspect, "sample_rows", fake_sample_rows)
    service = DatabaseService(Settings(db_max_cell_bytes=100))

    response = await service.sample_rows("t", 1)

    assert len(response["data"]["rows"][0]["doc"]) < 200
    assert response["meta"]["truncated"] is False


async def test_search_budget_is_shared_across_matches(monkeypatch: pytest.MonkeyPatch):
    def match(table: str) -> IdentifierMatch:
        rows = [{"id": i, "note": "n" * 100} for i in range(20)]
        return IdentifierMatch(table=table, column="order_id", source_type="live", rows=rows, row_count=len(rows))

    async def fake_search(identifier, id_type, settings, limit=None):
        return IdentifierSearchResult(
            identifier=identifier,
            id_type=id_type,
            searched_tables=[],
            truncated=False,
            matches=[match("public.a"), match("public.b"), match("public.c")],
            skipped=[],
            data_freshness_note=None,
        )

    monkeypatch.setattr(introspect, "search_by_identifier", fake_search)
    budget = 3_000
    service = DatabaseService(Settings(db_max_response_bytes=budget))

    response = await service.search_by_identifier("42", "order_id")

    matches = response["data"]["matches"]
    total_rows = sum(len(m["rows"]) for m in matches)
    assert all(m["row_count"] == len(m["rows"]) for m in matches)
    assert response["meta"]["row_count"] == total_rows < 60
    assert response["meta"]["truncated"] is True
    assert sum(_row_bytes(r) for m in matches for r in m["rows"]) <= budget


# --- DatabaseService: concurrent query slots ---


async def test_calls_beyond_concurrency_limit_fail_fast_with_server_busy(monkeypatch: pytest.MonkeyPatch):
    release = asyncio.Event()
    started = asyncio.Event()

    async def slow_list_tables(schema, settings):
        started.set()
        await release.wait()
        return []

    monkeypatch.setattr(introspect, "list_tables", slow_list_tables)
    service = DatabaseService(Settings(db_max_concurrent_queries=1, db_queue_timeout_s=0.05))

    first = asyncio.create_task(service.list_tables())
    await started.wait()
    busy = await service.list_tables()
    release.set()
    done = await first

    assert busy["ok"] is False
    assert busy["error"]["rule"] == "server_busy"
    assert done["ok"] is True


async def test_slot_is_released_after_an_error(monkeypatch: pytest.MonkeyPatch):
    from opsinsight.core.errors import GuardrailError

    async def failing(schema, settings):
        raise GuardrailError(rule="x", message="boom")

    monkeypatch.setattr(introspect, "list_tables", failing)
    service = DatabaseService(Settings(db_max_concurrent_queries=1, db_queue_timeout_s=0.05))

    for _ in range(3):  # would hit server_busy on the 2nd call if the slot leaked
        assert (await service.list_tables())["error"]["rule"] == "x"


async def test_search_fanout_runs_at_most_limit_at_once():
    running = peak = 0

    async def probe(i: int) -> int:
        nonlocal running, peak
        running += 1
        peak = max(peak, running)
        await asyncio.sleep(0.01)
        running -= 1
        return i

    results = await introspect._gather_bounded([probe(i) for i in range(12)], 3)

    assert results == list(range(12))  # order preserved, like asyncio.gather
    assert peak == 3


