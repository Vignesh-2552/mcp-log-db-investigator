import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from core.redaction import redact_rows
from integrations.database.engine import get_engine
from integrations.database.guardrail import truncate_cell, validate_sql
from tools.database import utils


@mcp.tool()
async def db_run_query(sql: str, limit: int = 200) -> dict:
    """Run a read-only SELECT against the replica. Returns rows, columns, row count, and elapsed ms."""
    settings = get_settings()
    try:
        validated = validate_sql(
            sql, settings.db_max_rows, requested_limit=limit
        )
        started = time.perf_counter()
        async with get_engine(settings).connect() as conn:
            result = await conn.execute(text(validated.sql))
            columns = list(result.keys())
            raw_rows = result.fetchall()
        elapsed_ms = (time.perf_counter() - started) * 1000
        utils.logger.info("Executed db_run_query: %d rows returned in %.2f ms", len(raw_rows), elapsed_ms)
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)

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
