import json

from investigation_server.server import mcp
from investigation_server.cloudwatch.tools import cw_list_log_groups


@mcp.resource("logs://groups")
def log_group_inventory() -> str:
    """Log group inventory, reusing cw_list_log_groups."""
    result = cw_list_log_groups()
    return json.dumps(result, indent=2, default=str)
