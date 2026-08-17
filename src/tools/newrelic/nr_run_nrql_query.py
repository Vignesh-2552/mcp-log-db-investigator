import httpx

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from core.redaction import redact_rows
from integrations.newrelic.guardrail import validate_nrql
from tools.newrelic import utils


@mcp.tool()
async def nr_run_nrql_query(query: str, limit: int = 100) -> dict:
    """Run a read-only NRQL query against New Relic (Log/Metric/event data).
    Only SELECT queries are permitted; LIMIT is enforced/clamped server-side."""
    settings = get_settings()
    try:
        validated_query = validate_nrql(query, settings.nr_max_rows, requested_limit=limit)
        result = await utils.run_nrql(validated_query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return utils.httpx_error_response(e)

    rows = result["results"]
    if rows and isinstance(rows[0], dict):
        rows = redact_rows(rows, settings)

    return {
        "ok": True,
        "data": {
            "executed_query": validated_query,
            "rows": rows,
            "row_count": len(rows),
            "metadata": result["metadata"],
        },
        "meta": {"row_count": len(rows)},
    }
