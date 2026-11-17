import json

from opsinsight.core.app import mcp
from opsinsight.core.container import get_container


def _render(response: dict) -> str:
    """The service's `data` on success; the full `{ok: false, error}` envelope
    (same shape the db_* tools return) on failure, so a read against an
    unreachable or misconfigured DB fails readably."""
    payload = response["data"] if response.get("ok") else response
    return json.dumps(payload, indent=2, default=str)


@mcp.resource("schema://db/tables")
async def schema_all_tables() -> str:
    """Compact JSON dump of every table."""
    return _render(await get_container().database_service.list_tables(None))


@mcp.resource("schema://db/table/{name}")
async def schema_table_detail(name: str) -> str:
    return _render(await get_container().database_service.describe_table(name))
