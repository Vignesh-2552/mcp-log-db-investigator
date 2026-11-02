import asyncio

import pytest
from starlette.testclient import TestClient

from opsinsight.core.app import mcp
from opsinsight.core.config import Settings
from opsinsight.core.transport_security import (
    STATIC_TOKEN_CLIENT_ID,
    BearerTokenVerifier,
    InsecureHttpConfigError,
    build_auth,
    http_run_kwargs,
    is_loopback_host,
)

TOKEN = "t" * 40

_INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "0"},
    },
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _settings(**overrides) -> Settings:
    return Settings(_env_file=None, **overrides)


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "::1", "[::1]", "127.0.0.5"])
def test_loopback_hosts_recognised(host):
    assert is_loopback_host(host)


@pytest.mark.parametrize("host", ["0.0.0.0", "10.0.0.5", "mcp.example.com", ""])
def test_non_loopback_hosts_recognised(host):
    assert not is_loopback_host(host)


def test_loopback_without_token_is_allowed_unauthenticated():
    assert build_auth(_settings(server_host="127.0.0.1")) is None


def test_non_loopback_without_token_refuses_to_start():
    with pytest.raises(InsecureHttpConfigError, match="MCP_AUTH_TOKEN"):
        build_auth(_settings(server_host="0.0.0.0"))


def test_non_loopback_without_token_allowed_only_with_explicit_opt_out():
    assert build_auth(_settings(server_host="0.0.0.0", allow_unauthenticated_http=True)) is None


def test_short_token_rejected():
    with pytest.raises(InsecureHttpConfigError, match="at least"):
        build_auth(_settings(mcp_auth_token="short"))


def test_token_builds_verifier_even_on_loopback():
    assert isinstance(build_auth(_settings(mcp_auth_token=TOKEN)), BearerTokenVerifier)


def test_token_error_message_never_echoes_token():
    secret = "s3cret-but-too-short"
    with pytest.raises(InsecureHttpConfigError) as exc:
        build_auth(_settings(mcp_auth_token=secret))
    assert secret not in str(exc.value)


def test_verifier_accepts_only_the_exact_token():
    verifier = BearerTokenVerifier(TOKEN)
    accepted = asyncio.run(verifier.verify_token(TOKEN))
    assert accepted is not None and accepted.client_id == STATIC_TOKEN_CLIENT_ID
    assert asyncio.run(verifier.verify_token(TOKEN[:-1])) is None
    assert asyncio.run(verifier.verify_token(TOKEN + "x")) is None
    assert asyncio.run(verifier.verify_token("")) is None


def test_http_kwargs_are_always_strict():
    kwargs = http_run_kwargs(_settings(server_allowed_hosts="mcp.example.com, other.example.com"))
    assert kwargs["host_origin_protection"] is True
    assert kwargs["allowed_hosts"] == ["mcp.example.com", "other.example.com"]
    assert "allowed_origins" not in kwargs


@pytest.fixture
def http_client():
    """The real server's HTTP app, with bearer auth and strict Host/Origin
    checks exactly as server.run() configures them."""
    settings = _settings(mcp_auth_token=TOKEN, server_allowed_hosts="mcp.example.com")
    previous_auth = mcp.auth
    mcp.auth = build_auth(settings)
    kwargs = http_run_kwargs(settings)
    app = mcp.http_app(
        path="/mcp",
        host_origin_protection=kwargs["host_origin_protection"],
        allowed_hosts=kwargs["allowed_hosts"],
    )
    try:
        with TestClient(app, base_url="http://localhost") as client:
            yield client
    finally:
        mcp.auth = previous_auth


def _post(client: TestClient, **headers: str):
    return client.post("/mcp", json=_INITIALIZE, headers={**_MCP_HEADERS, **headers})


def test_http_request_without_token_is_rejected(http_client):
    assert _post(http_client).status_code == 401


def test_http_request_with_wrong_token_is_rejected(http_client):
    assert _post(http_client, Authorization=f"Bearer {TOKEN[:-1]}x").status_code == 401


def test_http_request_with_token_succeeds(http_client):
    response = _post(http_client, Authorization=f"Bearer {TOKEN}")
    assert response.status_code == 200
    assert "investigation-server" in response.text


def test_dns_rebinding_host_is_rejected_even_with_valid_token(http_client):
    response = _post(http_client, Authorization=f"Bearer {TOKEN}", Host="attacker.example:8000")
    assert response.status_code == 421


def test_configured_public_host_is_accepted(http_client):
    response = _post(http_client, Authorization=f"Bearer {TOKEN}", Host="mcp.example.com")
    assert response.status_code == 200


def test_cross_origin_browser_request_is_rejected(http_client):
    response = _post(http_client, Authorization=f"Bearer {TOKEN}", Origin="https://attacker.example")
    assert response.status_code == 403
