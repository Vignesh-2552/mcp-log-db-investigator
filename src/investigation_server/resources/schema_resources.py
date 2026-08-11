import json

from investigation_server.app import mcp
from investigation_server.config import get_settings
from investigation_server.db import introspect
from investigation_server.errors import ToolError


@mcp.resource("schema://db/tables")
def schema_all_tables() -> str:
    """Compact JSON dump of every allowlisted table (doc §4.4)."""
    settings = get_settings()
    tables = introspect.list_tables(None, settings.db_table_allowlist_set)
    return json.dumps({"tables": tables}, indent=2, default=str)


@mcp.resource("schema://db/table/{name}")
def schema_table_detail(name: str) -> str:
    settings = get_settings()
    try:
        detail = introspect.describe_table(name, settings.db_table_allowlist_set)
    except ToolError as e:
        return json.dumps(e.to_response(), indent=2)
    return json.dumps(detail, indent=2, default=str)
