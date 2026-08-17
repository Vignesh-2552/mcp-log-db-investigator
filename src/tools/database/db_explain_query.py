from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from integrations.database.engine import get_engine
from integrations.database.guardrail import build_explain_sql, validate_sql
from tools.database import utils


@mcp.tool()
async def db_explain_query(sql: str) -> dict:
    """Run EXPLAIN (FORMAT JSON) on a validated SELECT. Run before expensive queries."""
    settings = get_settings()
    try:
        validated = validate_sql(sql, settings.db_max_rows)
        explain_sql = build_explain_sql(validated.sql)
        async with get_engine(settings).connect() as conn:
            plan = (await conn.execute(text(explain_sql))).scalar()
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)
    return {"ok": True, "data": {"executed_sql": explain_sql, "plan": plan}, "meta": {}}
