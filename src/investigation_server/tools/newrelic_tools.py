import httpx

from investigation_server.core.app import mcp
from investigation_server.core.config import get_settings
from investigation_server.core.errors import ToolError
from investigation_server.core.redaction import redact_rows
from investigation_server.integrations.newrelic.client import run_nrql
from investigation_server.integrations.newrelic.guardrail import validate_nrql


def _httpx_error_response(e: httpx.HTTPError) -> dict:
    if isinstance(e, httpx.HTTPStatusError):
        return ToolError(
            rule="newrelic_http_error",
            message=f"New Relic API returned HTTP {e.response.status_code}.",
            detail=e.response.text[:500],
        ).to_response()
    return ToolError(
        rule="newrelic_connection_error", message="Could not reach New Relic API.", detail=str(e)
    ).to_response()


@mcp.tool()
async def nr_run_nrql_query(query: str, limit: int = 100) -> dict:
    """Run a read-only NRQL query against New Relic (Log/Metric/event data).
    Only SELECT queries are permitted; LIMIT is enforced/clamped server-side."""
    settings = get_settings()
    try:
        validated_query = validate_nrql(query, settings.nr_max_rows, requested_limit=limit)
        result = await run_nrql(validated_query, settings)
    except ToolError as e:
        return e.to_response()
    except httpx.HTTPError as e:
        return _httpx_error_response(e)

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
