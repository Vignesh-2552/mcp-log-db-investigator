import hmac
from ipaddress import ip_address
from typing import Any

from fastmcp.server.auth import AccessToken, TokenVerifier

from opsinsight.core.config import Settings

# Shortest MCP_AUTH_TOKEN accepted; `python -c "import secrets; print(secrets.token_urlsafe(32))"`
# produces 43 characters.
MIN_AUTH_TOKEN_LENGTH = 32

# Identity recorded for requests authenticated with the shared MCP_AUTH_TOKEN.
STATIC_TOKEN_CLIENT_ID = "static-token"


class InsecureHttpConfigError(RuntimeError):
    """Raised at startup when the HTTP transport would be exposed unsafely."""


class BearerTokenVerifier(TokenVerifier):
    """Accepts exactly one shared bearer token (MCP_AUTH_TOKEN).

    Compares in constant time so the token can't be recovered byte-by-byte from
    response timing. Every caller gets the same identity: this is
    authentication for a single-team deployment, not per-user authorization —
    put an OAuth/JWT provider in front for that.
    """

    def __init__(self, token: str) -> None:
        super().__init__()
        self._token = token.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode(), self._token):
            return None
        return AccessToken(token=token, client_id=STATIC_TOKEN_CLIENT_ID, scopes=[])


def is_loopback_host(host: str) -> bool:
    if host.lower() == "localhost":
        return True
    try:
        return ip_address(host.strip("[]")).is_loopback
    except ValueError:
        return False


def build_auth(settings: Settings) -> BearerTokenVerifier | None:
    """The auth provider for the HTTP transport, or None if no token is set.
    Raises InsecureHttpConfigError if the configuration is unsafe to serve."""
    token = settings.mcp_auth_token.get_secret_value() if settings.mcp_auth_token else ""
    if token:
        if len(token) < MIN_AUTH_TOKEN_LENGTH:
            raise InsecureHttpConfigError(
                f"MCP_AUTH_TOKEN must be at least {MIN_AUTH_TOKEN_LENGTH} characters. Generate one with: "
                'python -c "import secrets; print(secrets.token_urlsafe(32))"'
            )
        return BearerTokenVerifier(token)

    if not is_loopback_host(settings.server_host) and not settings.allow_unauthenticated_http:
        raise InsecureHttpConfigError(
            f"Refusing to serve HTTP on non-loopback SERVER_HOST={settings.server_host!r} without "
            "authentication: anyone who can reach the port could query your database and logs. "
            "Set MCP_AUTH_TOKEN, or set ALLOW_UNAUTHENTICATED_HTTP=true only if an authenticating "
            "proxy in front of this server already enforces access."
        )
    return None


def http_run_kwargs(settings: Settings) -> dict[str, Any]:
    """Host/Origin (DNS-rebinding) protection for FastMCP's HTTP transport.

    Always strict, not FastMCP's default "auto" (which only checks when the
    process itself binds loopback): inside Docker the server binds 0.0.0.0, and
    "auto" would leave a port published on the host's 127.0.0.1 open to DNS
    rebinding from any web page the user visits. localhost/127.0.0.1/::1 are
    always accepted; SERVER_ALLOWED_HOSTS adds public hostnames.
    """
    kwargs: dict[str, Any] = {
        "host_origin_protection": True,
        "allowed_hosts": settings.server_allowed_hosts_list,
    }
    if settings.server_allowed_origins_list:
        kwargs["allowed_origins"] = settings.server_allowed_origins_list
    return kwargs
