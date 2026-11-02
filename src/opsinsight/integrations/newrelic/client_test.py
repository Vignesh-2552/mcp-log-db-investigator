"""run_nrql against a mocked NerdGraph endpoint (httpx.MockTransport)."""

import json

import httpx
import pytest

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.integrations.newrelic import client as nr_client


def _settings(**overrides) -> Settings:
    values = {"new_relic_api_key": "NRAK-test", "new_relic_account_id": "123"} | overrides
    return Settings(**values)


@pytest.fixture
def nerdgraph(monkeypatch: pytest.MonkeyPatch):
    """Routes run_nrql's httpx client to a handler the test sets."""
    state: dict = {"requests": [], "response": httpx.Response(200, json={})}

    def handler(request: httpx.Request) -> httpx.Response:
        state["requests"].append(request)
        return state["response"]

    real_client = httpx.AsyncClient

    def fake_client(*args, **kwargs):
        return real_client(*args, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(nr_client.httpx, "AsyncClient", fake_client)
    return state


def _ok_payload(results):
    return {"data": {"actor": {"account": {"nrql": {"results": results, "metadata": {"eventTypes": ["Log"]}}}}}}


async def test_run_nrql_posts_query_and_returns_results(nerdgraph):
    nerdgraph["response"] = httpx.Response(200, json=_ok_payload([{"count": 3}]))

    result = await nr_client.run_nrql("SELECT count(*) FROM Log", _settings())

    assert result == {"results": [{"count": 3}], "metadata": {"eventTypes": ["Log"]}}
    [request] = nerdgraph["requests"]
    assert str(request.url) == "https://api.newrelic.com/graphql"
    assert request.headers["Api-Key"] == "NRAK-test"
    body = json.loads(request.content)
    assert body["variables"] == {"accountId": 123, "nrql": "SELECT count(*) FROM Log"}


async def test_run_nrql_uses_eu_endpoint(nerdgraph):
    nerdgraph["response"] = httpx.Response(200, json=_ok_payload([]))
    await nr_client.run_nrql("SELECT 1 FROM Log", _settings(new_relic_region="eu"))
    assert str(nerdgraph["requests"][0].url) == "https://api.eu.newrelic.com/graphql"


@pytest.mark.parametrize(
    ("overrides", "rule"),
    [
        ({"new_relic_api_key": None}, "missing_credentials"),
        ({"new_relic_account_id": None}, "missing_credentials"),
        ({"new_relic_account_id": "acct-1"}, "invalid_account_id"),
    ],
)
async def test_run_nrql_rejects_bad_credentials_without_calling_api(nerdgraph, overrides, rule):
    with pytest.raises(ToolError) as exc:
        await nr_client.run_nrql("SELECT 1 FROM Log", _settings(**overrides))
    assert exc.value.rule == rule
    assert nerdgraph["requests"] == []


async def test_run_nrql_surfaces_graphql_errors(nerdgraph):
    nerdgraph["response"] = httpx.Response(200, json={"errors": [{"message": "NRQL syntax error"}]})
    with pytest.raises(ToolError) as exc:
        await nr_client.run_nrql("SELECT", _settings())
    assert exc.value.rule == "newrelic_api_error"
    assert "NRQL syntax error" in exc.value.detail


async def test_run_nrql_reports_missing_account(nerdgraph):
    nerdgraph["response"] = httpx.Response(200, json={"data": {"actor": {"account": None}}})
    with pytest.raises(ToolError) as exc:
        await nr_client.run_nrql("SELECT 1 FROM Log", _settings())
    assert "account data" in exc.value.message


async def test_run_nrql_raises_on_http_error(nerdgraph):
    nerdgraph["response"] = httpx.Response(401, text="unauthorized")
    with pytest.raises(httpx.HTTPStatusError):
        await nr_client.run_nrql("SELECT 1 FROM Log", _settings())


async def test_service_turns_http_errors_into_structured_responses(nerdgraph):
    from opsinsight.service.newrelic_service import NewRelicService

    nerdgraph["response"] = httpx.Response(403, text="forbidden")
    result = await NewRelicService(_settings()).run_nrql_query("SELECT * FROM Log")
    assert result["error"]["rule"] == "newrelic_http_error"
    assert "403" in result["error"]["message"]
