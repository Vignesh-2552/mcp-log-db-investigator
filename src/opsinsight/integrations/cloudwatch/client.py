import boto3

from opsinsight.core.config import Settings, get_settings
from opsinsight.core.logging_config import get_logger

logger = get_logger("cloudwatch.client")

# Keyed by the settings that shape the boto3 session (see _client_key), so a
# service built with different Settings gets its own client instead of
# silently reusing whichever one was created first.
_clients: dict[tuple, object] = {}


def _auth_mode(settings: Settings) -> str:
    if settings.cloudwatch_effective_access_key_id and settings.cloudwatch_effective_secret_access_key:
        return "access_key"
    if settings.aws_profile:
        return "profile"
    return "default_chain"


def _session(settings: Settings) -> boto3.Session:
    """CLOUDWATCH_ACCESS_KEY_ID/CLOUDWATCH_SECRET_ACCESS_KEY take priority
    over AWS_PROFILE when set in .env; falling back further to boto3's
    default credential chain (env vars it recognizes natively, instance
    role, etc.) if neither is configured. Region always comes from
    CLOUDWATCH_REGION — there is no AWS_REGION fallback."""
    access_key = settings.cloudwatch_effective_access_key_id
    secret_key = settings.cloudwatch_effective_secret_access_key
    if access_key and secret_key:
        return boto3.Session(
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key.get_secret_value(),
            region_name=settings.cloudwatch_effective_region,
        )
    return boto3.Session(profile_name=settings.aws_profile, region_name=settings.cloudwatch_effective_region)


def _client_key(service_name: str, settings: Settings) -> tuple:
    secret = settings.cloudwatch_effective_secret_access_key
    return (
        service_name,
        settings.cloudwatch_effective_region,
        settings.cloudwatch_effective_access_key_id,
        secret.get_secret_value() if secret else None,
        settings.aws_profile,
    )


def _get_client(service_name: str, label: str, settings: Settings | None):
    settings = settings or get_settings()
    key = _client_key(service_name, settings)
    client = _clients.get(key)
    if client is None:
        logger.info(
            "Initializing boto3 CloudWatch %s client (region=%s, auth=%s)",
            label,
            settings.cloudwatch_effective_region,
            _auth_mode(settings),
        )
        client = _clients[key] = _session(settings).client(service_name)
    return client


def get_logs_client(settings: Settings | None = None):
    """Lazily creates (and caches per credential set) a boto3 CloudWatch Logs client."""
    return _get_client("logs", "Logs", settings)


def get_metrics_client(settings: Settings | None = None):
    """Lazily creates (and caches per credential set) a boto3 CloudWatch Metrics client."""
    return _get_client("cloudwatch", "Metrics", settings)


def reset_clients() -> None:
    """Test helper to force client re-creation after settings change."""
    if _clients:
        logger.info("Resetting boto3 CloudWatch clients")
    _clients.clear()
