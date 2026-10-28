from opsinsight import resources as _resources  # noqa: F401
from opsinsight import tools as _tools  # noqa: F401
from opsinsight.core.app import mcp
from opsinsight.core.config import get_settings
from opsinsight.core.logging_config import get_logger, setup_logging
from opsinsight.core.transport_security import (
    InsecureHttpConfigError,
    build_auth,
    http_run_kwargs,
)

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

    try:
        mcp.auth = build_auth(settings)
    except InsecureHttpConfigError as e:
        logger.error("%s", e)
        raise SystemExit(1) from e
    if mcp.auth is None:
        logger.warning(
            "HTTP transport has NO authentication (SERVER_HOST=%s). Set MCP_AUTH_TOKEN to require a bearer token.",
            settings.server_host,
        )

    logger.info(
        "Starting Investigation FastMCP Server on %s:%s%s (transport: streamable-http, auth: %s, log_level: %s)",
        settings.server_host,
        settings.server_port,
        settings.server_path,
        "bearer-token" if mcp.auth else "none",
        settings.log_level,
    )
    mcp.run(
        transport="streamable-http",
        host=settings.server_host,
        port=settings.server_port,
        path=settings.server_path,
        **http_run_kwargs(settings),
    )


def main() -> None:
    """`investigation-server`: HTTP by default (Docker, hosted)."""
    run("streamable-http")


def main_stdio() -> None:
    """`opsinsight-mcp`: stdio by default, for clients that launch it via `uvx opsinsight-mcp`."""
    run("stdio")


if __name__ == "__main__":
    main()
