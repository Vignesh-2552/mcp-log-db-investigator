from unittest.mock import Mock, patch

from opsinsight.core.config import Settings
from opsinsight.integrations.database import engine as engine_module


def test_engine_log_masks_database_password(caplog):
    settings = Settings(db_url="postgresql+asyncpg://user:s3cr3t@example.com/app")
    fake_engine = Mock()

    engine_module._engines.clear()
    with (
        patch.object(engine_module, "create_async_engine", return_value=fake_engine),
        patch.object(engine_module, "_harden_session"),
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
        patch.object(engine_module, "_harden_session"),
    ):
        assert engine_module.get_engine(first) is engine_module.get_engine(first)
        assert engine_module.get_engine(first) is not engine_module.get_engine(second)
    engine_module._engines.clear()
