from core.config import Settings


def test_server_binds_to_loopback_by_default(monkeypatch):
    monkeypatch.delenv("SERVER_HOST", raising=False)

    assert Settings(_env_file=None).server_host == "127.0.0.1"


def test_platform_port_env_var_used_when_server_port_unset(monkeypatch):
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setenv("PORT", "10000")

    assert Settings(_env_file=None).server_port == 10000


def test_explicit_server_port_wins_over_platform_port(monkeypatch):
    monkeypatch.setenv("SERVER_PORT", "9000")
    monkeypatch.setenv("PORT", "10000")

    assert Settings(_env_file=None).server_port == 9000


def test_invalid_platform_port_falls_back_to_server_port(monkeypatch):
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setenv("PORT", "not-a-number")

    assert Settings(_env_file=None).server_port == 8000


def test_lowercase_server_port_env_var_still_wins_over_platform_port(monkeypatch):
    """SettingsConfigDict(case_sensitive=False) means an operator can set
    server_port (lowercase) and have pydantic-settings bind it — the $PORT
    fallback must recognize that as "explicitly set" too, not just the
    all-caps spelling."""
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setenv("server_port", "9000")
    monkeypatch.setenv("PORT", "10000")

    assert Settings(_env_file=None).server_port == 9000


def test_server_port_from_env_file_wins_over_platform_port(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("SERVER_PORT=9000\n", encoding="utf-8")
    monkeypatch.delenv("SERVER_PORT", raising=False)
    monkeypatch.setenv("PORT", "10000")

    assert Settings(_env_file=env_file).server_port == 9000
