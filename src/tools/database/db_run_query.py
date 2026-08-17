from core.app import mcp
from core.container import get_container


@mcp.tool()
async def db_run_query(sql: str, limit: int = 200) -> dict:
    """Run a read-only SELECT against the replica. Returns rows, columns, row count, and elapsed ms."""
    return await get_container().database_service.run_query(sql, limit)
