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


class _FakeCursor:
    def __init__(self, log: list[str]) -> None:
        self.log = log
        self.closed = False

    def execute(self, sql: str) -> None:
        self.log.append(sql)

    def close(self) -> None:
        self.closed = True


class _FakeDbapiConnection:
    def __init__(self) -> None:
        self.statements: list[str] = []
        self.cursors: list[_FakeCursor] = []

    def cursor(self) -> _FakeCursor:
        cursor = _FakeCursor(self.statements)
        self.cursors.append(cursor)
        return cursor


def test_every_new_connection_is_hardened():
    """The connect listener makes each session read-only with timeouts. The
    listener is captured and called with a fake DBAPI connection, so no
    database is needed."""
    settings = Settings(db_statement_timeout_ms=1234, db_idle_txn_timeout_ms=5678)
    listeners = {}

    def fake_listens_for(target, name):
        def register(fn):
            listeners[name] = fn
            return fn

        return register

    with patch.object(engine_module.event, "listens_for", fake_listens_for):
        engine_module._harden_session(Mock(), settings)
    conn = _FakeDbapiConnection()

    listeners["connect"](conn, None)

    assert conn.statements == [
        "SET default_transaction_read_only = on",
        "SET statement_timeout = '1234ms'",
        "SET idle_in_transaction_session_timeout = '5678ms'",
    ]
    assert conn.cursors[0].closed is True


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
