from core.app import mcp
from core.container import get_container


@mcp.tool()
def cw_filter_events(log_group: str, pattern: str, minutes: int = 30) -> dict:
    """Filter raw log events by pattern over the last `minutes`. Cheaper than Insights
    for simple greps."""
    return get_container().cloudwatch_service.filter_events(log_group, pattern, minutes)
