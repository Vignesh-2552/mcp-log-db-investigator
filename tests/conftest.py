import os
from collections.abc import Iterator

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from investigation_server.audit import get_audit_logger
from investigation_server.config import get_settings
from investigation_server.database.engine import reset_engine


@pytest.fixture
def settings_override(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch env vars and clear the Settings/engine caches so each
    test gets a fresh, isolated configuration."""

    def _apply(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key.upper(), value)
        get_settings.cache_clear()
        get_audit_logger.cache_clear()
        reset_engine()

    yield _apply
    get_settings.cache_clear()
    get_audit_logger.cache_clear()
    reset_engine()


def _postgres_available() -> bool:
    """Check whether the configured Postgres is reachable."""
    try:
        settings = get_settings()
        engine = create_engine(settings.db_dsn, connect_args={"connect_timeout": 2})
        with engine.connect():
            pass
        engine.dispose()
        return True
    except (OperationalError, Exception):
        return False


@pytest.fixture(scope="session")
def pg_available() -> bool:
    return _postgres_available()


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if os.environ.get("RUN_INTEGRATION_TESTS"):
        return
    if not _postgres_available():
        skip_marker = pytest.mark.skip(reason="Configured Postgres not reachable")
        for item in items:
            if "integration" in item.keywords:
                item.add_marker(skip_marker)
