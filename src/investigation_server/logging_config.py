import logging
import sys

from investigation_server.config import Settings, get_settings


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
    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Returns a logger namespaced under `investigation_server`."""
    if name.startswith("investigation_server."):
        return logging.getLogger(name)
    return logging.getLogger(f"investigation_server.{name}")
