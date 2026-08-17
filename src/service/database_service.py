import time

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.errors import ToolError
from core.logging_config import get_logger
from core.redaction import redact_rows
from integrations.database import introspect
from integrations.database.engine import get_engine
from integrations.database.guardrail import (
    build_explain_sql,
    sqlalchemy_error_detail,
    truncate_cell,
    validate_sql,
)
from service.base import BaseService

logger = get_logger("database.service")


class DatabaseService(BaseService):
    """Business logic for every DB-backed tool: validate -> execute ->
    redact -> structured response. `tools/database/*.py` are thin MCP
    wrappers that just construct this service and call a method."""

    def _sqlalchemy_error_response(self, e: SQLAlchemyError) -> dict:
        detail = sqlalchemy_error_detail(e)
        lowered = detail.lower()
        if "statement timeout" in lowered or "canceling statement" in lowered:
            logger.warning("Database query statement timeout: %s", detail)
            return ToolError(
                rule="query_timeout",
                message="Query exceeded the statement timeout.",
                detail=(
                    "Consider narrowing the time window or adding a more selective WHERE clause. "
                    "Use db_explain_query to inspect the plan before retrying."
                ),
            ).to_response()
        logger.error("Database query execution failed: %s", detail)
        return ToolError(
            rule="query_execution_error", message="Query failed to execute.", detail=detail
        ).to_response()

    async def list_tables(self, schema: str | None = None) -> dict:
        try:
            tables = await introspect.list_tables(schema)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok({"tables": tables}, {"row_count": len(tables)})

    async def describe_table(self, table: str) -> dict:
        try:
            detail = await introspect.describe_table(table)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok(detail)

    async def sample_rows(self, table: str, limit: int = 5) -> dict:
        try:
            result = await introspect.sample_rows(table, limit, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok(result, {"row_count": result["row_count"]})

    async def explain_query(self, sql: str) -> dict:
        try:
            validated = validate_sql(sql, self.settings.db_max_rows)
            explain_sql = build_explain_sql(validated.sql)
            async with get_engine(self.settings).connect() as conn:
                plan = (await conn.execute(text(explain_sql))).scalar()
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok({"executed_sql": explain_sql, "plan": plan})

    async def run_query(self, sql: str, limit: int = 200) -> dict:
        try:
            validated = validate_sql(sql, self.settings.db_max_rows, requested_limit=limit)
            started = time.perf_counter()
            async with get_engine(self.settings).connect() as conn:
                result = await conn.execute(text(validated.sql))
                columns = list(result.keys())
                raw_rows = result.fetchall()
            elapsed_ms = (time.perf_counter() - started) * 1000
            logger.info("Executed db_run_query: %d rows returned in %.2f ms", len(raw_rows), elapsed_ms)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)

        rows = [
            {col: truncate_cell(val, self.settings.db_max_cell_bytes) for col, val in zip(columns, row)}
            for row in raw_rows
        ]
        rows = redact_rows(rows, self.settings)
        return self.ok(
            {
                "rows": rows,
                "columns": columns,
                "row_count": len(rows),
                "elapsed_ms": round(elapsed_ms, 2),
                "executed_sql": validated.sql,
            },
            {"row_count": len(rows)},
        )

    async def search_by_identifier(self, identifier: str, id_type: str) -> dict:
        try:
            result = await introspect.search_by_identifier(identifier, id_type, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        total_rows = sum(m["row_count"] for m in result["matches"])
        return self.ok(result, {"row_count": total_rows})

    async def resolve_store(self, name_or_domain: str) -> dict:
        try:
            result = await introspect.resolve_store(name_or_domain, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok(result, {"candidate_count": len(result["candidates"])})
