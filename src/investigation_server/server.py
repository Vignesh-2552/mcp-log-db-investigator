# Imported for their registration side effects (@mcp.tool / @mcp.resource / @mcp.prompt).
from investigation_server import cloudwatch as _cloudwatch  # noqa: F401
from investigation_server import database as _database  # noqa: F401
from investigation_server import prompts as _prompts  # noqa: F401
from investigation_server import resources as _resources  # noqa: F401
from investigation_server.app import mcp
from investigation_server.config import get_settings


def main() -> None:
    settings = get_settings()
    mcp.run(
        transport="streamable-http",
        host=settings.server_host,
        port=settings.server_port,
        path=settings.server_path,
    )


if __name__ == "__main__":
    main()
