import httpx

from investigation_server.config import Settings, get_settings
from investigation_server.errors import ToolError
from investigation_server.logging_config import get_logger

logger = get_logger("newrelic.client")

_NRQL_GRAPHQL_QUERY = """
query($accountId: Int!, $nrql: Nrql!) {
  actor {
    account(id: $accountId) {
      nrql(query: $nrql) {
        results
        metadata {
          eventTypes
          facets
        }
      }
    }
  }
}
"""


async def run_nrql(query: str, settings: Settings | None = None) -> dict:
    """POSTs the given NRQL to New Relic's NerdGraph GraphQL API and returns
    {"results": [...], "metadata": {...}}."""
    settings = settings or get_settings()
    if not settings.new_relic_api_key or not settings.new_relic_account_id:
        raise ToolError(
            rule="missing_credentials",
            message="NEW_RELIC_API_KEY and NEW_RELIC_ACCOUNT_ID must be set in .env.",
        )

    logger.info("Running NRQL query against account %s", settings.new_relic_account_id)
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            settings.new_relic_graphql_url,
            headers={
                "Api-Key": settings.new_relic_api_key.get_secret_value(),
                "Content-Type": "application/json",
            },
            json={
                "query": _NRQL_GRAPHQL_QUERY,
                "variables": {"accountId": int(settings.new_relic_account_id), "nrql": query},
            },
        )
    response.raise_for_status()
    payload = response.json()

    if payload.get("errors"):
        raise ToolError(
            rule="newrelic_api_error",
            message="New Relic API returned an error.",
            detail=str(payload["errors"])[:1000],
        )

    account = ((payload.get("data") or {}).get("actor") or {}).get("account")
    if account is None:
        raise ToolError(
            rule="newrelic_api_error",
            message="New Relic API did not return account data. Check NEW_RELIC_ACCOUNT_ID and API key permissions.",
            detail=str(payload)[:1000],
        )

    nrql_result = account.get("nrql") or {}
    return {
        "results": nrql_result.get("results", []),
        "metadata": nrql_result.get("metadata", {}),
    }
