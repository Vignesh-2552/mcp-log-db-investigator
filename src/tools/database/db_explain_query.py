from core.app import mcp
from core.container import get_container


@mcp.tool()
async def db_explain_query(sql: str) -> dict:
    """Run EXPLAIN (FORMAT JSON) on a validated SELECT. Run before expensive queries."""
    return await get_container().database_service.explain_query(sql)
