from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.errors import ToolError
from integrations.database import introspect
from tools.database import utils


@mcp.tool()
async def db_list_tables(schema: str | None = None) -> dict:
    """List tables with row estimates and comments. Cached ~10 min."""
    try:
        tables = await introspect.list_tables(schema)
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)
    return {"ok": True, "data": {"tables": tables}, "meta": {"row_count": len(tables)}}
