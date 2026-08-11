from collections.abc import Generator
from contextlib import contextmanager

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Connection, Engine

from investigation_server.config import Settings, get_settings

_engine: Engine | None = None


def _harden_session(engine: Engine, settings: Settings) -> None:
    """Session hardening per design doc §6.1: read-only, statement timeout,
    idle-in-transaction timeout — applied on every new physical connection.
    This is defense in depth; the `mcp_ro` DB role is the last line of
    defence, not this application layer.
    """

    @event.listens_for(engine, "connect")
    def _on_connect(dbapi_conn, _connection_record) -> None:
        cursor = dbapi_conn.cursor()
        try:
            cursor.execute("SET default_transaction_read_only = on")
            cursor.execute(f"SET statement_timeout = '{settings.db_statement_timeout_ms}ms'")
            cursor.execute(
                f"SET idle_in_transaction_session_timeout = '{settings.db_idle_txn_timeout_ms}ms'"
            )
        finally:
            cursor.close()


def get_engine(settings: Settings | None = None) -> Engine:
    global _engine
    if _engine is None:
        settings = settings or get_settings()
        _engine = create_engine(settings.db_dsn, pool_pre_ping=True)
        _harden_session(_engine, settings)
    return _engine


def reset_engine() -> None:
    """Test helper: dispose of the cached engine so the next get_engine()
    call picks up fresh settings."""
    global _engine
    if _engine is not None:
        _engine.dispose()
    _engine = None


@contextmanager
def get_connection(settings: Settings | None = None) -> Generator[Connection]:
    engine = get_engine(settings)
    with engine.connect() as conn:
        yield conn
