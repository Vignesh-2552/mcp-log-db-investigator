from botocore.exceptions import BotoCoreError, ClientError

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.integrations.cloudwatch.client import get_metrics_client
from opsinsight.integrations.cloudwatch.guardrail import clamp_window
from opsinsight.integrations.cloudwatch.models import MetricStatsResult
from opsinsight.service.base import BaseService
from opsinsight.service.cloudwatch_common import aws_error_response
from opsinsight.service.ports import MetricsClient, MetricsClientFactory


class CloudWatchMetricsService(BaseService):
    """Business logic for the CloudWatch Metrics tool. The Metrics client
    factory is constructor-injected (defaulting to the real boto3 factory)
    so tests can pass a fake client instead of monkeypatching."""

    def __init__(
        self,
        settings: Settings,
        metrics_client_factory: MetricsClientFactory = get_metrics_client,
    ) -> None:
        super().__init__(settings)
        self._metrics_client_factory = metrics_client_factory

    def _metrics_client(self) -> MetricsClient:
        return self._metrics_client_factory(self.settings)

    _aws_error_response = staticmethod(aws_error_response)

    def get_metric_stats(
        self,
        namespace: str,
        metric: str,
        dimensions: dict[str, str],
        period: int,
        start: str,
        end: str,
    ) -> dict:
        settings = self.settings
        try:
            start_dt, end_dt = clamp_window(
                start, end, settings.cw_default_window_hours, settings.cw_max_window_hours
            )
            client = self._metrics_client()
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
            return self._aws_error_response(e)

        datapoints = sorted(response.get("Datapoints", []), key=lambda dp: dp["Timestamp"])
        for dp in datapoints:
            dp["Timestamp"] = dp["Timestamp"].isoformat()
        result = MetricStatsResult(namespace=namespace, metric=metric, datapoints=datapoints)
        return self.ok(result.to_dict(), {"row_count": len(datapoints)})
