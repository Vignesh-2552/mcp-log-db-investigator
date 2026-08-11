import json

from core.app import mcp
from tools.cloudwatch_tools import cw_list_log_groups


@mcp.resource("logs://groups")
def log_group_inventory() -> str:
    """Log group inventory, reusing cw_list_log_groups."""
    result = cw_list_log_groups()
    return json.dumps(result, indent=2, default=str)
