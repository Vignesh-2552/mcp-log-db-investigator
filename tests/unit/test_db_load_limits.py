"""Load-protection limits on the DB tools: response byte budget, concurrent
query slots, bounded search fan-out, and the request rate limiter."""

import asyncio
import json

import pytest
from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError

from opsinsight.core.config import Settings
from opsinsight.core.rate_limit import build_rate_limiter
from opsinsight.integrations.database import introspect
from opsinsight.integrations.database.guardrail import (
    fit_rows_to_budget,
    truncate_row_cells,
)
from opsinsight.integrations.database.models import (
    IdentifierMatch,
    IdentifierSearchResult,
    SampleRowsResult,
)
from opsinsight.service.database_service import DatabaseService


def _row_bytes(row: dict) -> int:
    return len(json.dumps(row, default=str).encode("utf-8")) + 1


# --- fit_rows_to_budget / truncate_row_cells (pure) ---


def test_rows_within_budget_are_all_kept():
    rows = [{"id": i} for i in range(10)]
    kept, truncated, used = fit_rows_to_budget(rows, 10_000)
    assert kept == rows
    assert truncated is False
    assert used == sum(_row_bytes(r) for r in rows)


def test_rows_past_budget_are_dropped_and_flagged():
    rows = [{"payload": "x" * 100} for _ in range(10)]
    budget = _row_bytes(rows[0]) * 3
    kept, truncated, used = fit_rows_to_budget(rows, budget)
    assert len(kept) == 3
    assert truncated is True
    assert used <= budget


def test_single_row_bigger_than_budget_returns_nothing():
    kept, truncated, _ = fit_rows_to_budget([{"blob": "x" * 500}], 100)
    assert kept == []
    assert truncated is True


def test_non_json_values_are_measured_as_strings():
    from datetime import datetime
    from decimal import Decimal

    kept, truncated, _ = fit_rows_to_budget([{"at": datetime(2026, 1, 1), "amount": Decimal("1.50")}], 1_000)
    assert len(kept) == 1
    assert truncated is False


def test_truncate_row_cells_caps_every_long_cell():
    row = truncate_row_cells({"a": "x" * 50, "b": "short", "c": 7}, 10)
    assert row["a"].startswith("x" * 10) and "truncated" in row["a"]
    assert row["b"] == "short"
    assert row["c"] == 7


# --- DatabaseService: byte budget on returned rows ---


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

    async def fake_search(identifier, id_type, settings):
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


# --- Request rate limiter ---


def test_rate_limiter_disabled_at_zero():
    assert build_rate_limiter(Settings(rate_limit_rps=0)) is None


def test_rate_limiter_uses_configured_rate_and_burst():
    limiter = build_rate_limiter(Settings(rate_limit_rps=2.5, rate_limit_burst=7))
    assert limiter.max_requests_per_second == 2.5
    assert limiter.burst_capacity == 7
    assert limiter.global_limit is True


async def test_rate_limiter_rejects_requests_past_the_burst():
    server = FastMCP("rate-limit-test")

    @server.tool()
    def ping() -> str:
        return "pong"

    # Near-zero refill: only the burst is available during the test. The
    # MCP initialize handshake itself consumes requests from the bucket.
    server.add_middleware(build_rate_limiter(Settings(rate_limit_rps=0.001, rate_limit_burst=4)))

    outcomes = []
    async with Client(server) as client:
        for _ in range(6):
            try:
                await client.call_tool("ping", {})
                outcomes.append("ok")
            except Exception as e:  # noqa: BLE001 - asserting on the error type below
                outcomes.append(e)

    assert outcomes[0] == "ok"
    rejected = [o for o in outcomes if o != "ok"]
    assert rejected, outcomes
    # A protocol-level error, not a tool result, so the call never ran.
    assert all(isinstance(e, MCPError) and "rate limit exceeded" in str(e).lower() for e in rejected)
