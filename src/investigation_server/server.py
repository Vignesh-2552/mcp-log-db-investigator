from investigation_server import cloudwatch as _cloudwatch  # noqa: F401
from investigation_server import database as _database  # noqa: F401
from investigation_server import newrelic as _newrelic  # noqa: F401
from investigation_server import prompts as _prompts  # noqa: F401
from investigation_server import resources as _resources  # noqa: F401
from investigation_server.app import mcp
from investigation_server.config import get_settings
from investigation_server.logging_config import get_logger, setup_logging

logger = get_logger("server")


def main() -> None:
    settings = get_settings()
    setup_logging(settings)
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


if __name__ == "__main__":
    main()

