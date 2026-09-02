import hmac

from fastmcp.server.auth.auth import AuthProvider, TokenVerifier
from mcp.server.auth.provider import AccessToken

from core.config import Settings
from core.logging_config import get_logger

logger = get_logger("auth")


class StaticTokenVerifier(TokenVerifier):
    """Verifies a single shared-secret bearer token (`MCP_AUTH_TOKEN`).

    This is a constant-time string compare, not an OAuth flow — it exists to
    put a minimal gate on the streamable-HTTP transport (which has no
    authentication of its own) when the server is reachable beyond loopback,
    e.g. a single-tenant deployment behind a public URL.
    """

    def __init__(self, expected_token: str):
        super().__init__()
        self._expected_token = expected_token

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token, self._expected_token):
            return None
        return AccessToken(token=token, client_id="static", scopes=[])


def build_auth_provider(settings: Settings) -> AuthProvider | None:
    """Builds the MCP auth provider from settings, or None if auth is disabled.

    Warns loudly if the server is configured to bind non-loopback without a
    token set, since that leaves the read-only DB/CloudWatch/New Relic tools
    reachable to anyone who finds the URL.
    """
    token = settings.mcp_auth_token.get_secret_value() if settings.mcp_auth_token is not None else None
    if token:
        logger.info("Bearer token authentication enabled for the MCP endpoint")
        return StaticTokenVerifier(token)

    if settings.mcp_auth_token is not None:
        logger.warning("MCP_AUTH_TOKEN is set to an empty value — treating it as unset.")

    if settings.server_host not in ("127.0.0.1", "localhost", "::1"):
        logger.warning(
            "SERVER_HOST=%s is non-loopback but MCP_AUTH_TOKEN is not set — "
            "the /mcp endpoint will accept requests with no authentication.",
            settings.server_host,
        )
    return None
