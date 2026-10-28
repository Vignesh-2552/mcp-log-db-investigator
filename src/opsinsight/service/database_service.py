import asyncio
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.logging_config import get_logger
from opsinsight.core.redaction import redact_rows
from opsinsight.integrations.database import introspect
from opsinsight.integrations.database.engine import get_engine
from opsinsight.integrations.database.guardrail import (
    build_explain_sql,
    fit_rows_to_budget,
    sqlalchemy_error_detail,
    truncate_row_cells,
    validate_sql,
)
from opsinsight.service.base import BaseService

logger = get_logger("database.service")

_TRUNCATION_HINT = (
    "Select fewer columns, aggregate (COUNT/GROUP BY), or add a more selective WHERE/LIMIT "
    "to get the rest."
)


class DatabaseService(BaseService):
    """Business logic for every DB-backed tool: validate -> execute ->
    redact -> structured response. `tools/database/*.py` are thin MCP
    wrappers that just construct this service and call a method."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        # Caps DB tool calls in flight for this service instance (the
        # container keeps one per process), so a burst of calls fails fast
        # with `server_busy` instead of queueing on the connection pool.
        self._query_slots = asyncio.Semaphore(settings.db_max_concurrent_queries)

    @asynccontextmanager
    async def _query_slot(self) -> AsyncIterator[None]:
        try:
            await asyncio.wait_for(self._query_slots.acquire(), self.settings.db_queue_timeout_s)
        except TimeoutError:
            logger.warning(
                "DB concurrency limit (%d) reached; rejecting call after %.1fs wait",
                self.settings.db_max_concurrent_queries,
                self.settings.db_queue_timeout_s,
            )
            raise ToolError(
                rule="server_busy",
                message="Too many database queries are running right now.",
                detail="Retry in a few seconds; avoid issuing many DB queries in parallel.",
            ) from None
        try:
            yield
        finally:
            self._query_slots.release()

    def _cap_rows(self, rows: list[dict[str, Any]], budget: int) -> tuple[list[dict[str, Any]], bool, int]:
        """Truncates oversized cells, then keeps the rows that fit `budget` bytes."""
        cells_capped = [truncate_row_cells(row, self.settings.db_max_cell_bytes) for row in rows]
        return fit_rows_to_budget(cells_capped, budget)

    def _truncation_meta(self, returned: int, fetched: int) -> dict:
        return {
            "truncated": True,
            "truncation_note": (
                f"Response capped at {self.settings.db_max_response_bytes} bytes: returned {returned} "
                f"of {fetched} fetched rows. {_TRUNCATION_HINT}"
            ),
        }

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
            async with self._query_slot():
                tables = await introspect.list_tables(schema, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok({"tables": [t.to_dict() for t in tables]}, {"row_count": len(tables)})

    async def describe_table(self, table: str) -> dict:
        try:
            async with self._query_slot():
                detail = await introspect.describe_table(table, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok(detail.to_dict())

    async def sample_rows(self, table: str, limit: int = 5) -> dict:
        try:
            async with self._query_slot():
                result = await introspect.sample_rows(table, limit, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        fetched = len(result.rows)
        result.rows, truncated, _ = self._cap_rows(result.rows, self.settings.db_max_response_bytes)
        result.row_count = len(result.rows)
        meta: dict = {"row_count": result.row_count, "truncated": truncated}
        if truncated:
            meta.update(self._truncation_meta(result.row_count, fetched))
        return self.ok(result.to_dict(), meta)

    async def explain_query(self, sql: str) -> dict:
        try:
            validated = validate_sql(sql, self.settings.db_max_rows)
            explain_sql = build_explain_sql(validated.sql)
            async with self._query_slot(), get_engine(self.settings).connect() as conn:
                plan = (await conn.execute(text(explain_sql))).scalar()
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok({"executed_sql": explain_sql, "plan": plan})

    async def run_query(self, sql: str, limit: int = 200) -> dict:
        try:
            validated = validate_sql(sql, self.settings.db_max_rows, requested_limit=limit)
            async with self._query_slot():
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

        rows = redact_rows([dict(zip(columns, row)) for row in raw_rows], self.settings)
        rows, truncated, _ = self._cap_rows(rows, self.settings.db_max_response_bytes)
        meta: dict = {"row_count": len(rows), "truncated": truncated}
        if truncated:
            meta.update(self._truncation_meta(len(rows), len(raw_rows)))
        return self.ok(
            {
                "rows": rows,
                "columns": columns,
                "row_count": len(rows),
                "elapsed_ms": round(elapsed_ms, 2),
                "executed_sql": validated.sql,
            },
            meta,
        )

    async def search_by_identifier(self, identifier: str, id_type: str) -> dict:
        try:
            async with self._query_slot():
                result = await introspect.search_by_identifier(identifier, id_type, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        # One byte budget shared across every matched table, in match order.
        remaining = self.settings.db_max_response_bytes
        fetched = returned = 0
        truncated = False
        for match in result.matches:
            fetched += len(match.rows)
            match.rows, cut, used = self._cap_rows(match.rows, remaining)
            match.row_count = len(match.rows)
            remaining -= used
            returned += match.row_count
            truncated = truncated or cut
        meta: dict = {"row_count": returned, "truncated": truncated}
        if truncated:
            meta.update(self._truncation_meta(returned, fetched))
        return self.ok(result.to_dict(), meta)

    async def resolve_store(self, name_or_domain: str) -> dict:
        try:
            async with self._query_slot():
                result = await introspect.resolve_store(name_or_domain, self.settings)
        except ToolError as e:
            return e.to_response()
        except SQLAlchemyError as e:
            return self._sqlalchemy_error_response(e)
        return self.ok(result.to_dict(), {"candidate_count": len(result.candidates)})
