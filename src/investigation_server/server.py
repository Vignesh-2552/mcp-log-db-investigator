from investigation_server.app import mcp

# Imported for their registration side effects (@mcp.tool/@mcp.resource/@mcp.prompt).
from investigation_server import cw as _cw  # noqa: F401
from investigation_server import db as _db  # noqa: F401
from investigation_server import prompts as _prompts  # noqa: F401
from investigation_server import resources as _resources  # noqa: F401


def main() -> None:
    mcp.run()  # stdio transport (Phase 1/2 scope only; no HTTP)


if __name__ == "__main__":
    main()
