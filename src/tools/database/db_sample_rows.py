from core.app import mcp
from core.container import get_container


@mcp.tool()
async def db_sample_rows(table: str, limit: int = 5) -> dict:
    """Return sample rows from a table (PII-masked) to learn value formats, e.g. status enums."""
    return await get_container().database_service.sample_rows(table, limit)
