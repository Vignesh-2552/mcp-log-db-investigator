import json

from sqlalchemy.exc import SQLAlchemyError

from opsinsight.core.app import mcp
from opsinsight.core.errors import ToolError
from opsinsight.integrations.database import introspect
from opsinsight.integrations.database.guardrail import sqlalchemy_error_detail


def _error_json(e: ToolError | SQLAlchemyError) -> str:
    """Same `{ok: false, error}` shape the db_* tools return, so a resource
    read against an unreachable or misconfigured DB fails readably instead of
    surfacing a raw driver traceback."""
    if isinstance(e, SQLAlchemyError):
        e = ToolError(
            rule="query_execution_error",
            message="Schema lookup failed.",
            detail=sqlalchemy_error_detail(e),
        )
    return json.dumps(e.to_response(), indent=2)


@mcp.resource("schema://db/tables")
async def schema_all_tables() -> str:
    """Compact JSON dump of every table."""
    try:
        tables = await introspect.list_tables(None)
    except (ToolError, SQLAlchemyError) as e:
        return _error_json(e)
    return json.dumps({"tables": [table.to_dict() for table in tables]}, indent=2, default=str)


@mcp.resource("schema://db/table/{name}")
async def schema_table_detail(name: str) -> str:
    try:
        detail = await introspect.describe_table(name)
    except (ToolError, SQLAlchemyError) as e:
        return _error_json(e)
    return json.dumps(detail.to_dict(), indent=2, default=str)
