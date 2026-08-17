from core.app import mcp
from core.container import get_container


@mcp.tool()
async def db_list_tables(schema: str | None = None) -> dict:
    """List tables with row estimates and comments. Cached ~10 min."""
    return await get_container().database_service.list_tables(schema)
