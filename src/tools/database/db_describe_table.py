from core.app import mcp
from core.container import get_container


@mcp.tool()
async def db_describe_table(table: str) -> dict:
    """Describe a table's columns, types, nullability, PK/FK, and indexes.
    The main grounding tool for generating a query against unfamiliar schema."""
    return await get_container().database_service.describe_table(table)
