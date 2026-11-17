from typing import Any

from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from opsinsight.core.config import Settings, get_settings
from opsinsight.core.logging_config import get_logger

logger = get_logger("database.engine")

# Keyed by DSN + session-hardening settings, so a service built with
# different Settings gets its own engine instead of silently reusing
# whichever one was created first.
_engines: dict[tuple[str, int, int], AsyncEngine] = {}


def session_hardening_connect_args(settings: Settings) -> dict[str, Any]:
    """Session hardening: read-only, statement timeout, idle-in-transaction
    timeout. Defense in depth; the DB role is the last line of defence.

    Sent as asyncpg `server_settings`, i.e. in the connection's startup
    packet, so they become the session defaults. They must not be applied
    with `SET` from a pool "connect" listener: SQLAlchemy's asyncpg adapter
    runs that `SET` inside its implicit transaction, and the rollback at the
    end of the first checkout reverts it, leaving every later checkout of
    that pooled connection with no timeout and read-write defaults.
    """
    return {
        "server_settings": {
            "default_transaction_read_only": "on",
            "statement_timeout": f"{settings.db_statement_timeout_ms}ms",
            "idle_in_transaction_session_timeout": f"{settings.db_idle_txn_timeout_ms}ms",
        }
    }


def get_engine(settings: Settings | None = None) -> AsyncEngine:
    settings = settings or get_settings()
    key = (settings.db_dsn, settings.db_statement_timeout_ms, settings.db_idle_txn_timeout_ms)
    engine = _engines.get(key)
    if engine is None:
        logger.info(
            "Initializing DB engine for DSN: %s (read_only=on, statement_timeout=%dms, idle_txn_timeout=%dms)",
            make_url(settings.db_dsn).render_as_string(hide_password=True),
            settings.db_statement_timeout_ms,
            settings.db_idle_txn_timeout_ms,
        )
        engine = create_async_engine(
            settings.db_dsn,
            pool_pre_ping=True,
            connect_args=session_hardening_connect_args(settings),
        )
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
