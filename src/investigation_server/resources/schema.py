import json

from investigation_server.server import mcp
from investigation_server.database import introspect
from investigation_server.errors import ToolError


@mcp.resource("schema://db/tables")
def schema_all_tables() -> str:
    """Compact JSON dump of every table."""
    tables = introspect.list_tables(None)
    return json.dumps({"tables": tables}, indent=2, default=str)


@mcp.resource("schema://db/table/{name}")
def schema_table_detail(name: str) -> str:
    try:
        detail = introspect.describe_table(name)
    except ToolError as e:
        return json.dumps(e.to_response(), indent=2)
    return json.dumps(detail, indent=2, default=str)
