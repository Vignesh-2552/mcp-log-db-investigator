import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from investigation_server.app import mcp
from investigation_server.audit import audited
from investigation_server.config import get_settings
from investigation_server.db import introspect
from investigation_server.db.engine import get_engine
from investigation_server.db.guardrail import build_explain_sql, truncate_cell, validate_sql
from investigation_server.errors import ToolError
from investigation_server.redaction import redact_rows


def _sqlalchemy_error_response(e: SQLAlchemyError) -> dict:
    detail = str(e.orig) if getattr(e, "orig", None) else str(e)
    lowered = detail.lower()
    if "statement timeout" in lowered or "canceling statement" in lowered:
        return ToolError(
            rule="query_timeout",
            message="Query exceeded the statement timeout.",
            detail=(
                "Consider narrowing the time window or adding a more selective WHERE clause. "
                "Use db_explain_query to inspect the plan before retrying."
            ),
        ).to_response()
    return ToolError(rule="query_execution_error", message="Query failed to execute.", detail=detail).to_response()


@mcp.tool()
@audited("db_list_tables")
def db_list_tables(schema: str | None = None) -> dict:
    """List allowlisted tables with row estimates and comments. Cached ~10 min."""
    settings = get_settings()
    try:
        tables = introspect.list_tables(schema, settings.db_table_allowlist_set)
    except ToolError as e:
        return e.to_response()
    return {"ok": True, "data": {"tables": tables}, "meta": {"row_count": len(tables)}}


@mcp.tool()
@audited("db_describe_table")
def db_describe_table(table: str) -> dict:
    """Describe a table's columns, types, nullability, PK/FK, and indexes.
    The main grounding tool for generating a query against unfamiliar schema."""
    settings = get_settings()
    try:
        detail = introspect.describe_table(table, settings.db_table_allowlist_set)
    except ToolError as e:
        return e.to_response()
    return {"ok": True, "data": detail, "meta": {}}


@mcp.tool()
@audited("db_sample_rows")
def db_sample_rows(table: str, limit: int = 5) -> dict:
    """Return sample rows from a table (PII-masked) to learn value formats, e.g. status enums."""
    settings = get_settings()
    try:
        result = introspect.sample_rows(table, limit, settings.db_table_allowlist_set, settings)
    except ToolError as e:
        return e.to_response()
    return {"ok": True, "data": result, "meta": {"row_count": result["row_count"]}}


@mcp.tool()
@audited("db_explain_query")
def db_explain_query(sql: str) -> dict:
    """Run EXPLAIN (FORMAT JSON) on a validated SELECT. Run before expensive queries."""
    settings = get_settings()
    try:
        validated = validate_sql(sql, settings.db_table_allowlist_set, settings.db_max_rows)
        explain_sql = build_explain_sql(validated.sql)
        with get_engine(settings).connect() as conn:
            plan = conn.execute(text(explain_sql)).scalar()
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return _sqlalchemy_error_response(e)
    return {"ok": True, "data": {"executed_sql": explain_sql, "plan": plan}, "meta": {}}


@mcp.tool()
@audited("db_run_query")
def db_run_query(sql: str, limit: int = 200) -> dict:
    """Run a read-only SELECT against the replica. Returns rows, columns, row count, and elapsed ms."""
    settings = get_settings()
    try:
        validated = validate_sql(
            sql, settings.db_table_allowlist_set, settings.db_max_rows, requested_limit=limit
        )
        started = time.perf_counter()
        with get_engine(settings).connect() as conn:
            result = conn.execute(text(validated.sql))
            columns = list(result.keys())
            raw_rows = result.fetchall()
        elapsed_ms = (time.perf_counter() - started) * 1000
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return _sqlalchemy_error_response(e)

    rows = [
        {col: truncate_cell(val, settings.db_max_cell_bytes) for col, val in zip(columns, row)}
        for row in raw_rows
    ]
    rows = redact_rows(rows, settings)
    return {
        "ok": True,
        "data": {
            "rows": rows,
            "columns": columns,
            "row_count": len(rows),
            "elapsed_ms": round(elapsed_ms, 2),
            "executed_sql": validated.sql,
        },
        "meta": {"row_count": len(rows)},
    }


@mcp.tool()
@audited("db_search_by_identifier")
def db_search_by_identifier(identifier: str, id_type: str) -> dict:
    """Search allowlisted tables for rows matching an identifier (order_id, user_id, payment_id, request_id)."""
    settings = get_settings()
    try:
        result = introspect.search_by_identifier(identifier, id_type, settings.db_table_allowlist_set, settings)
    except ToolError as e:
        return e.to_response()
    total_rows = sum(m["row_count"] for m in result["matches"])
    return {"ok": True, "data": result, "meta": {"row_count": total_rows}}
