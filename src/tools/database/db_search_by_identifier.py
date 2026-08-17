from core.app import mcp
from core.container import get_container


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
    return await get_container().database_service.search_by_identifier(identifier, id_type)
