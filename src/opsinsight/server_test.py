"""server.run(): transport choice, auth, rate limiter. mcp.run is replaced,
so nothing actually starts listening."""

import pytest

from opsinsight import server
from opsinsight.core.app import mcp
from opsinsight.core.config import get_settings


@pytest.fixture
def started(monkeypatch: pytest.MonkeyPatch):
    calls: dict = {"run": [], "middleware": []}
    monkeypatch.setattr(mcp, "run", lambda **kwargs: calls["run"].append(kwargs))
    monkeypatch.setattr(mcp, "add_middleware", lambda m: calls["middleware"].append(m))
    monkeypatch.setattr(mcp, "auth", None)
    monkeypatch.setattr(server, "setup_logging", lambda settings: None)
    for name in ("MCP_TRANSPORT", "MCP_AUTH_TOKEN", "SERVER_HOST", "RATE_LIMIT_RPS", "PORT"):
        monkeypatch.delenv(name, raising=False)
    get_settings.cache_clear()
    yield calls
    get_settings.cache_clear()


def _env(monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    for key, value in values.items():
        monkeypatch.setenv(key, value)
    get_settings.cache_clear()


def test_main_stdio_runs_stdio_transport(started, monkeypatch):
    _env(monkeypatch, MCP_AUTH_TOKEN="")
    server.main_stdio()
    assert started["run"] == [{"transport": "stdio", "show_banner": False}]


def test_main_runs_http_on_loopback(started, monkeypatch):
    _env(monkeypatch, MCP_AUTH_TOKEN="", SERVER_HOST="127.0.0.1", SERVER_PORT="9001")
    server.main()
    [kwargs] = started["run"]
    assert kwargs["transport"] == "streamable-http"
    assert kwargs["host"] == "127.0.0.1"
    assert kwargs["port"] == 9001
    assert mcp.auth is None


def test_mcp_transport_setting_overrides_entry_point_default(started, monkeypatch):
    _env(monkeypatch, MCP_TRANSPORT="stdio", MCP_AUTH_TOKEN="")
    server.main()
    assert started["run"][0]["transport"] == "stdio"


def test_http_with_token_enables_bearer_auth(started, monkeypatch):
    _env(monkeypatch, MCP_AUTH_TOKEN="t" * 40, SERVER_HOST="127.0.0.1")
    server.main()
    assert mcp.auth is not None
    assert started["run"][0]["transport"] == "streamable-http"


def test_non_loopback_http_without_token_refuses_to_start(started, monkeypatch):
    _env(monkeypatch, MCP_AUTH_TOKEN="", SERVER_HOST="0.0.0.0", ALLOW_UNAUTHENTICATED_HTTP="false")
    with pytest.raises(SystemExit) as exc:
        server.main()
    assert exc.value.code == 1
    assert started["run"] == []


def test_rate_limiter_is_added_unless_disabled(started, monkeypatch):
    _env(monkeypatch, MCP_AUTH_TOKEN="", RATE_LIMIT_RPS="5")
    server.main_stdio()
    assert len(started["middleware"]) == 1

    started["middleware"].clear()
    _env(monkeypatch, RATE_LIMIT_RPS="0")
    server.main_stdio()
    assert started["middleware"] == []
