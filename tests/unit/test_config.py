from core.config import Settings


def test_server_binds_to_loopback_by_default(monkeypatch):
    monkeypatch.delenv("SERVER_HOST", raising=False)

    assert Settings(_env_file=None).server_host == "127.0.0.1"
