"""core.limits: cell truncation and the JSON byte budget."""

import json

from opsinsight.core.limits import fit_rows_to_budget, truncate_row_cells


def _row_bytes(row: dict) -> int:
    return len(json.dumps(row, default=str).encode("utf-8")) + 1


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


