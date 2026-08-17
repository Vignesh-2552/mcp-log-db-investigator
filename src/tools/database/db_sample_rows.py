from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from integrations.database import introspect
from tools.database import utils


@mcp.tool()
async def db_sample_rows(table: str, limit: int = 5) -> dict:
    """Return sample rows from a table (PII-masked) to learn value formats, e.g. status enums."""
    settings = get_settings()
    try:
        result = await introspect.sample_rows(table, limit, settings)
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)
    return {"ok": True, "data": result, "meta": {"row_count": result["row_count"]}}
