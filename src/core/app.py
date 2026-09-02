from fastmcp import FastMCP

from core.auth import build_auth_provider
from core.config import get_settings
from core.logging_config import get_logger

logger = get_logger("app")

# The single shared FastMCP instance. Lives in its own module (nothing else)
# so that submodules can `from core.app import mcp` without ever circularly
# importing `server` — which matters because `server.py` is also the file
# run directly / via `-m` to start the
# process, and Python gives a script run as `__main__` (or via `-m`) a
# *different* module identity than the same file imported by its dotted
# path. If other modules import `mcp` from `server`, running `server.py`
# re-triggers the whole registration chain under a second module identity
# and blows up with a circular ImportError partway through.
mcp = FastMCP("investigation-server", auth=build_auth_provider(get_settings()))
logger.debug("Initialized FastMCP instance: investigation-server")

