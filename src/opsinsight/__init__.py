"""opsinsight-mcp: read-only MCP server for support ticket investigation
across PostgreSQL, AWS CloudWatch and New Relic."""

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("opsinsight-mcp")
except PackageNotFoundError:  # running from a source tree without an install
    __version__ = "0.0.0"
