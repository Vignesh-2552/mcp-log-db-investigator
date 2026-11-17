from unittest.mock import Mock, patch

from opsinsight.core.config import Settings
from opsinsight.integrations.database import engine as engine_module


def test_engine_log_masks_database_password(caplog):
    settings = Settings(db_url="postgresql+asyncpg://user:s3cr3t@example.com/app")
    fake_engine = Mock()

    engine_module._engines.clear()
    with (
        patch.object(engine_module, "create_async_engine", return_value=fake_engine),
        caplog.at_level("INFO"),
    ):
        engine_module.get_engine(settings)

    assert "s3cr3t" not in caplog.text
    assert "***" in caplog.text
    engine_module._engines.clear()


def test_engine_is_cached_per_dsn():
    first = Settings(db_url="postgresql+asyncpg://u@host-a/app")
    second = Settings(db_url="postgresql+asyncpg://u@host-b/app")

    engine_module._engines.clear()
    with (
        patch.object(engine_module, "create_async_engine", side_effect=lambda *a, **k: Mock()),
    ):
        assert engine_module.get_engine(first) is engine_module.get_engine(first)
        assert engine_module.get_engine(first) is not engine_module.get_engine(second)
    engine_module._engines.clear()


def test_every_connection_gets_hardening_as_server_settings():
    """Hardening goes in asyncpg's startup packet, so it is the session default
    and survives the rollback at the end of each pooled checkout."""
    settings = Settings(db_statement_timeout_ms=1234, db_idle_txn_timeout_ms=5678)
    calls = []

    engine_module._engines.clear()
    with patch.object(engine_module, "create_async_engine", side_effect=lambda *a, **k: calls.append(k) or Mock()):
        engine_module.get_engine(settings)
    engine_module._engines.clear()

    assert calls[0]["connect_args"]["server_settings"] == {
        "default_transaction_read_only": "on",
        "statement_timeout": "1234ms",
        "idle_in_transaction_session_timeout": "5678ms",
    }


async def test_reset_engine_disposes_cached_engines():
    disposed = []

    class _Engine:
        async def dispose(self):
            disposed.append(self)

    engine_module._engines.clear()
    engine_module._engines[("a", 1, 1)] = _Engine()
    engine_module._engines[("b", 1, 1)] = _Engine()

    await engine_module.reset_engine()

    assert len(disposed) == 2
    assert engine_module._engines == {}
