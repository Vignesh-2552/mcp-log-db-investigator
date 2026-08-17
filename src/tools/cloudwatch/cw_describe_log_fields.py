from core.app import mcp
from core.container import get_container


@mcp.tool()
def cw_describe_log_fields(log_group: str) -> dict:
    """Sample recent events from a log group and report discovered JSON shapes
    (clustered by top-level key set, since one log group can hold structurally
    different event types — e.g. access logs vs. app-error logs) with each
    shape's own field-frequency table, to ground Logs Insights query
    generation. Boosts the sample with error/fatal-level events so rare-but-
    important shapes aren't drowned out by a purely random sample."""
    return get_container().cloudwatch_service.describe_log_fields(log_group)
