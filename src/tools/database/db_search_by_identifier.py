from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from integrations.database import introspect
from tools.database import utils


@mcp.tool()
async def db_search_by_identifier(identifier: str, id_type: str) -> dict:
    """Search for rows matching an identifier. `id_type` is any column-name-like
    string (e.g. "order_id", "customer_id", "transaction_id") — resolved
    dynamically against the DB's own catalog, not a fixed list. Searches every
    table with a matching column name, plus the entity's own table (e.g.
    "order_id" also checks the "orders" table's primary key). Each searched
    table/match is tagged `source_type: "live"|"historical"` (schemas prefixed
    per DB_HISTORICAL_SCHEMA_PREFIXES, e.g. "migration"), and a top-level
    `data_freshness_note` warns when a 'no matches' result only reflects
    historical/migration-snapshot tables — check both before concluding an
    identifier doesn't exist anywhere."""
    settings = get_settings()
    try:
        result = await introspect.search_by_identifier(identifier, id_type, settings)
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)
    total_rows = sum(m["row_count"] for m in result["matches"])
    return {"ok": True, "data": result, "meta": {"row_count": total_rows}}
