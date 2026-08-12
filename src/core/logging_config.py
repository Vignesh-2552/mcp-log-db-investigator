import logging
import sys

from anyio import BrokenResourceError

from core.config import Settings, get_settings


class _BenignClientDisconnectFilter(logging.Filter):
    """Drops the mcp SDK's known-benign streamable-HTTP client-disconnect noise:
    a client that closes its connection right after a notification-only POST
    (202 Accepted, no reply owed) can race the server's queued session-message
    write, which the SDK's broad `except Exception` logs as an ERROR traceback
    and then tries to answer on the now-closed connection, cascading into a
    second RuntimeError from uvicorn. Neither indicates a server-side bug — it's
    an open upstream issue (modelcontextprotocol/python-sdk#1648, #2741, #3142)
    not yet fixed on the 1.x line `fastmcp` currently pins (`mcp<2.0`). Matches
    narrowly on exception type/message so unrelated real errors still surface.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.exc_info:
            return True
        exc_type, exc, _ = record.exc_info
        is_broken_resource = exc_type is BrokenResourceError
        is_late_asgi_send = exc_type is RuntimeError and "after response already completed" in str(exc)
        return not (is_broken_resource or is_late_asgi_send)


def _install_benign_disconnect_filters() -> None:
    disconnect_filter = _BenignClientDisconnectFilter()
    for logger_name in ("mcp.server.streamable_http", "uvicorn.error"):
        third_party_logger = logging.getLogger(logger_name)
        if not any(isinstance(f, _BenignClientDisconnectFilter) for f in third_party_logger.filters):
            third_party_logger.addFilter(disconnect_filter)


def setup_logging(settings: Settings | None = None) -> logging.Logger:
    """Configures package-level logging for `investigation_server`.

    Logs are routed to sys.stderr to ensure compatibility with stdio-based
    MCP transports (where sys.stdout is reserved for JSON-RPC messages).
    """
    settings = settings or get_settings()

    level_name = settings.log_level.upper()
    level = getattr(logging, level_name, logging.INFO)

    root_logger = logging.getLogger("investigation_server")
    root_logger.setLevel(level)

    # Avoid duplicate handlers if setup_logging is called multiple times
    if not root_logger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(level)
        formatter = logging.Formatter(settings.log_format)
        handler.setFormatter(formatter)
        root_logger.addHandler(handler)

    root_logger.propagate = False

    _install_benign_disconnect_filters()

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Returns a logger namespaced under `investigation_server`."""
    if name.startswith("investigation_server."):
        return logging.getLogger(name)
    return logging.getLogger(f"investigation_server.{name}")
