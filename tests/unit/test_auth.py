from core.auth import StaticTokenVerifier, build_auth_provider
from core.config import Settings


async def test_correct_token_verifies():
    verifier = StaticTokenVerifier("secret-token")

    result = await verifier.verify_token("secret-token")

    assert result is not None
    assert result.token == "secret-token"


async def test_wrong_token_rejected():
    verifier = StaticTokenVerifier("secret-token")

    assert await verifier.verify_token("wrong-token") is None


async def test_empty_token_rejected():
    verifier = StaticTokenVerifier("secret-token")

    assert await verifier.verify_token("") is None


def test_build_auth_provider_none_when_token_unset():
    settings = Settings(_env_file=None, server_host="127.0.0.1")

    assert build_auth_provider(settings) is None


def test_build_auth_provider_returns_verifier_when_token_set(monkeypatch):
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret-token")
    settings = Settings(_env_file=None)

    provider = build_auth_provider(settings)

    assert isinstance(provider, StaticTokenVerifier)


def test_build_auth_provider_warns_on_non_loopback_without_token(monkeypatch, caplog):
    settings = Settings(_env_file=None, server_host="0.0.0.0")

    with caplog.at_level("WARNING", logger="investigation_server.auth"):
        provider = build_auth_provider(settings)

    assert provider is None
    assert "no authentication" in caplog.text


def test_build_auth_provider_treats_empty_token_as_unset(monkeypatch, caplog):
    """An empty MCP_AUTH_TOKEN (e.g. a blank value left in a platform's env
    var UI) must not silently become a verifier that only accepts an empty
    bearer token — that would lock out every real client with no signal."""
    monkeypatch.setenv("MCP_AUTH_TOKEN", "")
    settings = Settings(_env_file=None, server_host="127.0.0.1")

    with caplog.at_level("WARNING", logger="investigation_server.auth"):
        provider = build_auth_provider(settings)

    assert provider is None
    assert "empty" in caplog.text
