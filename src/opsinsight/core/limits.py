"""Response-size limits shared by every row-returning tool: per-cell
truncation and a JSON byte budget for a list of rows."""

import datetime
import decimal
import ipaddress
import json
import uuid
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


# Types FastMCP/pydantic serialize natively; anything else is stringified.
_JSON_NATIVE = (
    str, int, float, bool, type(None),
    datetime.datetime, datetime.date, datetime.time, datetime.timedelta,
    decimal.Decimal, uuid.UUID,
    ipaddress.IPv4Address, ipaddress.IPv6Address,
    ipaddress.IPv4Network, ipaddress.IPv6Network,
    ipaddress.IPv4Interface, ipaddress.IPv6Interface,
)


def json_safe(value: Any) -> Any:
    r"""Makes a DB cell serializable by the MCP response encoder. `bytes`
    (bytea) become UTF-8 text when valid, else Postgres-style hex (`\x...`);
    driver objects with no JSON form (ranges, bit strings, geometric types)
    become `str(value)`. Apply before redaction so decoded text is scanned."""
    if isinstance(value, _JSON_NATIVE):
        return value
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return "\\x" + data.hex()
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    return str(value)


def unique_column_names(columns: list[str]) -> list[str]:
    """`["id", "id", "name"]` -> `["id", "id_2", "name"]`, so rows built as
    dicts don't silently drop one of two same-named columns in a join."""
    seen: set[str] = set(columns)
    counts: dict[str, int] = {}
    out: list[str] = []
    for name in columns:
        counts[name] = counts.get(name, 0) + 1
        if counts[name] == 1:
            out.append(name)
            continue
        n = counts[name]
        candidate = f"{name}_{n}"
        while candidate in seen:
            n += 1
            candidate = f"{name}_{n}"
        seen.add(candidate)
        out.append(candidate)
    return out


def rows_from_result(columns: list[str], raw_rows: Any) -> tuple[list[str], list[dict[str, Any]]]:
    """Builds JSON-safe row dicts with unique keys from a driver result."""
    names = unique_column_names(columns)
    return names, [{k: json_safe(v) for k, v in zip(names, row, strict=False)} for row in raw_rows]
