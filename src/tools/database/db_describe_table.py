from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.errors import ToolError
from integrations.database import introspect
from tools.database import utils


@mcp.tool()
async def db_describe_table(table: str) -> dict:
    """Describe a table's columns, types, nullability, PK/FK, and indexes.
    The main grounding tool for generating a query against unfamiliar schema."""
    try:
        detail = await introspect.describe_table(table)
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)
    return {"ok": True, "data": detail, "meta": {}}
