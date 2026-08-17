from core.app import mcp
from core.container import get_container


@mcp.tool()
def cw_get_metric_stats(
    namespace: str,
    metric: str,
    dimensions: dict[str, str],
    period: int,
    start: str,
    end: str,
) -> dict:
    """Get metric datapoints (avg/sum/min/max/sample_count) for correlating error spikes
    with infra metrics like CPU/latency."""
    return get_container().cloudwatch_service.get_metric_stats(
        namespace, metric, dimensions, period, start, end
    )
