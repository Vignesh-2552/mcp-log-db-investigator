from core.app import mcp
from core.container import get_container


@mcp.tool()
async def nr_run_nrql_query(query: str, limit: int = 100) -> dict:
    """Run a read-only NRQL query against New Relic (Log/Metric/event data).
    Only SELECT queries are permitted; LIMIT is enforced/clamped server-side."""
    return await get_container().newrelic_service.run_nrql_query(query, limit)
