from sqlalchemy.exc import SQLAlchemyError

from core.errors import ToolError
from core.logging_config import get_logger
from integrations.database.guardrail import sqlalchemy_error_detail

logger = get_logger("database.tools")


def sqlalchemy_error_response(e: SQLAlchemyError) -> dict:
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
    return ToolError(rule="query_execution_error", message="Query failed to execute.", detail=detail).to_response()
