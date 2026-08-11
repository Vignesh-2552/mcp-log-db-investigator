import boto3

from investigation_server.config import Settings, get_settings

_logs_client = None
_metrics_client = None


def _session(settings: Settings) -> boto3.Session:
    return boto3.Session(profile_name=settings.aws_profile, region_name=settings.aws_region)


def get_logs_client(settings: Settings | None = None):
    """Lazily creates a boto3 CloudWatch Logs client. Pointing at a real AWS
    account is just AWS_PROFILE/AWS_REGION — no code change (doc §6.2)."""
    global _logs_client
    if _logs_client is None:
        settings = settings or get_settings()
        _logs_client = _session(settings).client("logs")
    return _logs_client


def get_metrics_client(settings: Settings | None = None):
    global _metrics_client
    if _metrics_client is None:
        settings = settings or get_settings()
        _metrics_client = _session(settings).client("cloudwatch")
    return _metrics_client


def reset_clients() -> None:
    """Test helper to force client re-creation after settings change."""
    global _logs_client, _metrics_client
    _logs_client = None
    _metrics_client = None
