from unittest.mock import Mock, patch

from core.config import Settings
from integrations.database import engine as engine_module


def test_engine_log_masks_database_password(caplog):
    settings = Settings(db_url="postgresql+asyncpg://user:s3cr3t@example.com/app")
    fake_engine = Mock()

    engine_module._engine = None
    with (
        patch.object(engine_module, "create_async_engine", return_value=fake_engine),
        patch.object(engine_module, "_harden_session"),
        caplog.at_level("INFO"),
    ):
        engine_module.get_engine(settings)

    assert "s3cr3t" not in caplog.text
    assert "***" in caplog.text
    engine_module._engine = None
