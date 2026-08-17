from sqlalchemy.exc import SQLAlchemyError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from integrations.database import introspect
from tools.database import utils


@mcp.tool()
async def db_resolve_store(name_or_domain: str) -> dict:
    """Resolve a store name or domain (e.g. "olallawines.com") to its store_id.
    Searches catalog-discovered identifier-like columns (domain, hostname,
    store_name, slug, subdomain — configurable via DB_STORE_IDENTIFIER_COLUMNS)
    with case-insensitive partial (ILIKE) matching; no hardcoded "stores"
    table. When multiple distinct store_ids match, the response is
    `ambiguous: true` with every match listed in `candidates` rather than
    silently picking one — always check `ambiguous` before trusting a single
    `store_id`."""
    settings = get_settings()
    try:
        result = await introspect.resolve_store(name_or_domain, settings)
    except ToolError as e:
        return e.to_response()
    except SQLAlchemyError as e:
        return utils.sqlalchemy_error_response(e)
    return {"ok": True, "data": result, "meta": {"candidate_count": len(result["candidates"])}}
