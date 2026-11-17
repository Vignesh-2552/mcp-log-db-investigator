"""Helpers shared by the CloudWatch Logs and Metrics services."""

from opsinsight.core.errors import ToolError
from opsinsight.core.logging_config import get_logger
from opsinsight.core.redaction import redact_text

logger = get_logger("cloudwatch.service")


def aws_error_response(e: Exception) -> dict:
    """Translates a boto3 `BotoCoreError` / `ClientError` into the standard error envelope."""
    logger.error("AWS CloudWatch API error: %s", e)
    return ToolError(rule="aws_error", message="AWS CloudWatch call failed.", detail=redact_text(str(e))).to_response()
