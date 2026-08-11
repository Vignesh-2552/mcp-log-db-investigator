import boto3

from core.config import Settings, get_settings
from core.logging_config import get_logger

logger = get_logger("cloudwatch.client")

_logs_client = None
_metrics_client = None


def _auth_mode(settings: Settings) -> str:
    if settings.cloudwatch_effective_access_key_id and settings.cloudwatch_effective_secret_access_key:
        return "access_key"
    if settings.aws_profile:
        return "profile"
    return "default_chain"


def _session(settings: Settings) -> boto3.Session:
    """CLOUDWATCH_ACCESS_KEY_ID/CLOUDWATCH_SECRET_ACCESS_KEY (or the AWS_*
    fallbacks) take priority over AWS_PROFILE when set in .env; falling back
    further to boto3's default credential chain (env vars it recognizes
    natively, instance role, etc.) if neither is configured."""
    access_key = settings.cloudwatch_effective_access_key_id
    secret_key = settings.cloudwatch_effective_secret_access_key
    if access_key and secret_key:
        return boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key.get_secret_value(),
            region_name=settings.cloudwatch_effective_region,
        )
    return boto3.Session(profile_name=settings.aws_profile, region_name=settings.cloudwatch_effective_region)


def get_logs_client(settings: Settings | None = None):
    """Lazily creates a boto3 CloudWatch Logs client."""
    global _logs_client
    if _logs_client is None:
        settings = settings or get_settings()
        logger.info(
            "Initializing boto3 CloudWatch Logs client (region=%s, auth=%s)",
            settings.cloudwatch_effective_region,
            _auth_mode(settings),
        )
        _logs_client = _session(settings).client("logs")
    return _logs_client


def get_metrics_client(settings: Settings | None = None):
    global _metrics_client
    if _metrics_client is None:
        settings = settings or get_settings()
        logger.info(
            "Initializing boto3 CloudWatch Metrics client (region=%s, auth=%s)",
            settings.cloudwatch_effective_region,
            _auth_mode(settings),
        )
        _metrics_client = _session(settings).client("cloudwatch")
    return _metrics_client


def reset_clients() -> None:
    """Test helper to force client re-creation after settings change."""
    global _logs_client, _metrics_client
    if _logs_client is not None or _metrics_client is not None:
        logger.info("Resetting boto3 CloudWatch clients")
    _logs_client = None
    _metrics_client = None
