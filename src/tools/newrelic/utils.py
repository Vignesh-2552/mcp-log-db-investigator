import re

import httpx

from core.errors import ToolError
from integrations.newrelic.client import run_nrql

__all__ = ["CORRELATION_ID_RE", "EVENT_TYPE_RE", "httpx_error_response", "run_nrql"]

EVENT_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CORRELATION_ID_RE = re.compile(
    r"(trace|span|request|correlation|order|user|session|transaction)[._-]?id", re.IGNORECASE
)


def httpx_error_response(e: httpx.HTTPError) -> dict:
    if isinstance(e, httpx.HTTPStatusError):
        return ToolError(
            rule="newrelic_http_error",
            message=f"New Relic API returned HTTP {e.response.status_code}.",
            detail=e.response.text[:500],
        ).to_response()
    return ToolError(
        rule="newrelic_connection_error", message="Could not reach New Relic API.", detail=str(e)
    ).to_response()
