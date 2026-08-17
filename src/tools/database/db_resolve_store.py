from core.app import mcp
from core.container import get_container


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
    return await get_container().database_service.resolve_store(name_or_domain)
