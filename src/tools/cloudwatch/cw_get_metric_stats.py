from botocore.exceptions import BotoCoreError, ClientError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from tools.cloudwatch import utils


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
    settings = get_settings()
    try:
        start_dt, end_dt = utils.clamp_window(
            start, end, settings.cw_default_window_hours, settings.cw_max_window_hours
        )
        client = utils.get_metrics_client(settings)
        response = client.get_metric_statistics(
            Namespace=namespace,
            MetricName=metric,
            Dimensions=[{"Name": k, "Value": v} for k, v in dimensions.items()],
            StartTime=start_dt,
            EndTime=end_dt,
            Period=period,
            Statistics=["Average", "Sum", "Minimum", "Maximum", "SampleCount"],
        )
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return utils.aws_error_response(e)

    datapoints = sorted(response.get("Datapoints", []), key=lambda dp: dp["Timestamp"])
    for dp in datapoints:
        dp["Timestamp"] = dp["Timestamp"].isoformat()
    return {
        "ok": True,
        "data": {"namespace": namespace, "metric": metric, "datapoints": datapoints},
        "meta": {"row_count": len(datapoints)},
    }
