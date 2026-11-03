import asyncio
import sys

from opsinsight.core.config import get_settings
from opsinsight.core.logging_config import get_logger, setup_logging

logger = get_logger("server")

_USAGE = """\
Usage: {prog} [command]

Commands:
  doctor     Run diagnostic pre-flight checks
  (default)  Start the MCP server

Options:
  --help, -h       Show this help message
  --version, -v    Show the version number
"""


def _handle_cli_flags(prog: str) -> bool:
    """Handle ``doctor``, ``--help``, ``--version`` before starting the
    MCP server.  Returns ``True`` if the process should exit afterwards."""
    args = sys.argv[1:]

    if not args:
        return False

    first = args[0].lower()

    if first in ("--help", "-h"):
        print(_USAGE.format(prog=prog), file=sys.stderr)
        raise SystemExit(0)

    if first in ("--version", "-v"):
        import opsinsight

        print(f"opsinsight-mcp {opsinsight.__version__}", file=sys.stderr)
        raise SystemExit(0)

    if first == "doctor":
        from opsinsight.doctor import run_doctor

        code = asyncio.run(run_doctor(get_settings()))
        raise SystemExit(code)

    return False


def run(default_transport: str) -> None:
    from opsinsight import prompts as _prompts  # noqa: F401
    from opsinsight import resources as _resources  # noqa: F401
    from opsinsight import tools as _tools  # noqa: F401
    from opsinsight.core.app import mcp
    from opsinsight.core.rate_limit import build_rate_limiter
    from opsinsight.core.transport_security import (
        InsecureHttpConfigError,
        build_auth,
        http_run_kwargs,
    )

    settings = get_settings()
    setup_logging(settings)
    transport = settings.mcp_transport or default_transport

    rate_limiter = build_rate_limiter(settings)
    if rate_limiter is not None:
        mcp.add_middleware(rate_limiter)
        logger.info(
            "Rate limit: %.2f req/s, burst %d (global)",
            rate_limiter.max_requests_per_second,
            rate_limiter.burst_capacity,
        )
    else:
        logger.warning("Rate limiting disabled (RATE_LIMIT_RPS=0)")

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
    _handle_cli_flags("investigation-server")
    run("streamable-http")


def main_stdio() -> None:
    """`opsinsight-mcp`: stdio by default, for clients that launch it via `uvx opsinsight-mcp`."""
    _handle_cli_flags("opsinsight-mcp")
    run("stdio")


if __name__ == "__main__":
    main()
