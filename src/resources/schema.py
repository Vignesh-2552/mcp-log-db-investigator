import json

from core.app import mcp
from core.errors import ToolError
from integrations.database import introspect


@mcp.resource("schema://db/tables")
async def schema_all_tables() -> str:
    """Compact JSON dump of every table."""
    tables = await introspect.list_tables(None)
    return json.dumps({"tables": tables}, indent=2, default=str)


@mcp.resource("schema://db/table/{name}")
async def schema_table_detail(name: str) -> str:
    try:
        detail = await introspect.describe_table(name)
    except ToolError as e:
        return json.dumps(e.to_response(), indent=2)
    return json.dumps(detail, indent=2, default=str)
