from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy import event
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine, create_async_engine

from opsinsight.core.config import Settings, get_settings
from opsinsight.core.logging_config import get_logger

logger = get_logger("database.engine")

# Keyed by DSN + session-hardening settings, so a service built with
# different Settings gets its own engine instead of silently reusing
# whichever one was created first.
_engines: dict[tuple[str, int, int], AsyncEngine] = {}


def _harden_session(engine: AsyncEngine, settings: Settings) -> None:
    """Session hardening: read-only, statement timeout,
    idle-in-transaction timeout — applied on every new physical connection.
    This is defense in depth; the DB role is the last line of defence,
    not this application layer.

    SQLAlchemy's asyncpg dialect exposes a DBAPI-style sync `cursor()` on
    the pool "connect" event even though the underlying driver is async
    (via greenlet trampolining), so this listener works the same way it
    would for a sync driver.
    """

    @event.listens_for(engine.sync_engine, "connect")
    def _on_connect(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("SET default_transaction_read_only = on")
            cursor.execute(f"SET statement_timeout = '{settings.db_statement_timeout_ms}ms'")
            cursor.execute(
                f"SET idle_in_transaction_session_timeout = '{settings.db_idle_txn_timeout_ms}ms'"
            )
            logger.debug(
                "Applied DB session hardening (read_only=on, statement_timeout=%dms, idle_txn_timeout=%dms)",
                settings.db_statement_timeout_ms,
                settings.db_idle_txn_timeout_ms,
            )
        finally:
            cursor.close()


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    key = (settings.db_dsn, settings.db_statement_timeout_ms, settings.db_idle_txn_timeout_ms)
    engine = _engines.get(key)
    if engine is None:
        logger.info(
            "Initializing DB engine for DSN: %s",
            make_url(settings.db_dsn).render_as_string(hide_password=True),
        )
        engine = create_async_engine(settings.db_dsn, pool_pre_ping=True)
        _harden_session(engine, settings)
        _engines[key] = engine
    return engine


async def reset_engine() -> None:
    """Test helper: dispose of every cached engine so the next get_engine()
    call picks up fresh settings."""
    engines = list(_engines.values())
    _engines.clear()
    for engine in engines:
        logger.info("Disposing DB engine")
        await engine.dispose()


@asynccontextmanager
async def get_connection(settings: Settings | None = None) -> AsyncGenerator[AsyncConnection]:
    engine = get_engine(settings)
    async with engine.connect() as conn:
        yield conn
