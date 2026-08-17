from core.app import mcp
from core.container import get_container


@mcp.tool()
def cw_list_log_groups(prefix: str | None = None) -> dict:
    """List log groups (name, retention, stored bytes), filtered to the allowlist."""
    return get_container().cloudwatch_service.list_log_groups(prefix)
