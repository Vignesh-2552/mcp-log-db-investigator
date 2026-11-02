"""Response-size limits shared by every row-returning tool: per-cell
truncation and a JSON byte budget for a list of rows."""

import json
from typing import Any


def truncate_cell(value: object, max_bytes: int = 4096) -> object:
    """Truncates str/bytes cells over max_bytes."""
    if isinstance(value, str):
        data = value.encode("utf-8")
        if len(data) > max_bytes:
            return data[:max_bytes].decode("utf-8", errors="ignore") + f"...[truncated {len(data)}b]"
        return value
    if isinstance(value, bytes):
        if len(value) > max_bytes:
            return value[:max_bytes] + f"...[truncated {len(value)}b]".encode()
        return value
    return value


def truncate_row_cells(row: dict[str, Any], max_bytes: int) -> dict[str, Any]:
    return {col: truncate_cell(val, max_bytes) for col, val in row.items()}


def fit_rows_to_budget(rows: list[dict[str, Any]], max_bytes: int) -> tuple[list[dict[str, Any]], bool, int]:
    """Keeps leading rows while their JSON-serialized size fits in max_bytes.
    Returns (kept_rows, truncated, bytes_used). Sizes are measured the way
    the rows go out on the wire (JSON, non-JSON values as str)."""
    kept: list[dict[str, Any]] = []
    used = 0
    for row in rows:
        size = len(json.dumps(row, default=str).encode("utf-8")) + 1  # +1 for the separator
        if used + size > max_bytes:
            return kept, True, used
        kept.append(row)
        used += size
    return kept, False, used
