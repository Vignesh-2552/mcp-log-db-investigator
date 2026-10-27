import resources as _resources  # noqa: F401
import tools as _tools  # noqa: F401
from core.app import mcp
from core.config import get_settings
from core.logging_config import get_logger, setup_logging

logger = get_logger("server")


def run(default_transport: str) -> None:
    settings = get_settings()
    setup_logging(settings)
    transport = settings.mcp_transport or default_transport

    if transport == "stdio":
        # stdout carries JSON-RPC in stdio mode; logging already goes to stderr.
        logger.info(
            "Starting Investigation FastMCP Server (transport: stdio, log_level: %s)",
            settings.log_level,
        )
        mcp.run(transport="stdio", show_banner=False)
        return

    logger.info(
        "Starting Investigation FastMCP Server on %s:%s%s (transport: streamable-http, log_level: %s)",
        settings.server_host,
        settings.server_port,
        settings.server_path,
        settings.log_level,
    )
    mcp.run(
        transport="streamable-http",
        host=settings.server_host,
        port=settings.server_port,
        path=settings.server_path,
    )


def main() -> None:
    """`investigation-server`: HTTP by default (Docker, hosted)."""
    run("streamable-http")


def main_stdio() -> None:
    """`opsinsight-mcp`: stdio by default, for clients that launch it via `uvx opsinsight-mcp`."""
    run("stdio")


if __name__ == "__main__":
    main()
