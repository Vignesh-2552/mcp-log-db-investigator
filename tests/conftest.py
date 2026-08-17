import asyncio
import os

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import create_async_engine

from core.config import get_settings
from core.container import reset_container
from integrations.database.engine import reset_engine


@pytest.fixture
async def settings_override(monkeypatch: pytest.MonkeyPatch):
    """Monkeypatch env vars and clear the Settings/engine/container caches so
    each test gets a fresh, isolated configuration."""

    async def _apply(**env: str) -> None:
        for key, value in env.items():
            monkeypatch.setenv(key.upper(), value)
        get_settings.cache_clear()
        reset_container()
        await reset_engine()

    yield _apply
    get_settings.cache_clear()
    reset_container()
    await reset_engine()


async def _check_postgres() -> bool:
    """Check whether the configured Postgres is reachable. Only treats
    genuine connectivity failures as "unreachable" (-> skip integration
    tests) — a broken driver/config (e.g. missing asyncpg) is a real bug
    and is allowed to raise so it fails loudly instead of being silently
    skipped.

    asyncpg can raise a raw TimeoutError/OSError for network-level
    failures (connect timeout, refused, unreachable) before SQLAlchemy
    gets a chance to wrap it as OperationalError — those count as
    "unreachable" too, same as OperationalError. Anything else (e.g.
    ModuleNotFoundError for a missing driver) still propagates."""
    settings = get_settings()
    engine = create_async_engine(settings.db_dsn, connect_args={"timeout": 2})
    try:
        async with engine.connect():
            pass
        return True
    except (OperationalError, TimeoutError, OSError):
        return False
    finally:
        await engine.dispose()


def _postgres_available() -> bool:
    return asyncio.run(_check_postgres())


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
