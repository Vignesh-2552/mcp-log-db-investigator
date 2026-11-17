import base64
import json

import httpx
import pytest
from pydantic import SecretStr

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.integrations.tickets.jira_client import JiraClient


def _settings(**overrides) -> Settings:
    values = {
        "jira_base_url": "https://acme.atlassian.net/",
        "jira_email": "bot@acme.test",
        "jira_api_token": SecretStr("jira-token"),
        **overrides,
    }
    return Settings(_env_file=None, **values)


def _client(handler, settings: Settings | None = None) -> tuple[JiraClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    return JiraClient(settings or _settings(), transport=httpx.MockTransport(_record)), seen


ADF = {"type": "doc", "version": 1, "content": [{"type": "paragraph", "content": [{"type": "text", "text": "Pay fails"}]}]}

ISSUE = {
    "id": "10001",
    "key": "SUP-7",
    "fields": {
        "summary": "Checkout broken",
        "description": ADF,
        "status": {"name": "Open"},
        "assignee": {"accountId": "acc-1", "displayName": "Ravi", "emailAddress": "ravi@acme.test"},
        "reporter": {"accountId": "acc-2", "displayName": "Customer Desk"},
        "created": "2026-11-01T10:00:00.000+0000",
        "updated": "2026-11-02T10:00:00.000+0000",
        "priority": {"name": "High"},
        "labels": ["checkout", 5],
        "issuetype": {"name": "Bug"},
        "project": {"key": "SUP"},
    },
}


async def test_get_issue_uses_basic_auth_and_flattens_adf():
    client, seen = _client(lambda r: httpx.Response(200, json=ISSUE))

    ticket = await client.get_issue("SUP-7")

    [request] = seen
    assert request.url.path == "/rest/api/3/issue/SUP-7"
    assert request.url.host == "acme.atlassian.net"
    expected = base64.b64encode(b"bot@acme.test:jira-token").decode()
    assert request.headers["Authorization"] == f"Basic {expected}"
    assert ticket.description == "Pay fails"
    assert ticket.url == "https://acme.atlassian.net/browse/SUP-7"
    assert ticket.status == "Open"
    assert ticket.labels == ["checkout"]
    assert ticket.issue_type == "Bug"
    assert ticket.assignee.to_dict() == {"display_name": "Ravi", "account_id": "acc-1"}  # no email


async def test_404_propagates_as_http_error():
    client, _ = _client(lambda r: httpx.Response(404, json={"errorMessages": ["Issue does not exist"]}))
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await client.get_issue("SUP-404")
    assert exc.value.response.status_code == 404


async def test_search_uses_the_jql_endpoint_with_escaped_text():
    body = {"issues": [{"id": "1", "key": "SUP-1", "fields": {"summary": "s", "status": {"name": "Open"},
                                                            "updated": "u", "assignee": None}}],
            "isLast": True}
    client, seen = _client(lambda r: httpx.Response(200, json=body))

    hits = await client.search_issues('pay "fails"', None, 5)

    [request] = seen
    assert request.method == "POST"
    assert request.url.path == "/rest/api/3/search/jql"
    sent = json.loads(request.content)
    assert sent["jql"] == 'text ~ "pay \\"fails\\"" ORDER BY updated DESC'
    assert sent["maxResults"] == 5
    assert hits[0].key == "SUP-1"
    assert hits[0].url == "https://acme.atlassian.net/browse/SUP-1"


async def test_search_passes_raw_jql():
    client, seen = _client(lambda r: httpx.Response(200, json={"issues": []}))
    assert await client.search_issues(None, "project = SUP ORDER BY created", 10) == []
    assert json.loads(seen[0].content)["jql"] == "project = SUP ORDER BY created"


async def test_list_comments_flattens_bodies_and_reports_total():
    body = {"comments": [{"id": 9, "author": {"displayName": "Ravi", "accountId": "a", "emailAddress": "x@y.z"},
                          "body": ADF, "created": "c", "updated": "u"}], "total": 4}
    client, seen = _client(lambda r: httpx.Response(200, json=body))

    page = await client.list_comments("SUP-7", 1)

    assert seen[0].url.params["maxResults"] == "1"
    assert page.comments[0].body == "Pay fails"
    assert page.comments[0].id == "9"
    assert page.total == 4
    assert page.has_more is True


async def test_unconfigured_client_refuses_before_any_request():
    client, seen = _client(lambda r: httpx.Response(200, json={}), settings=_settings(jira_api_token=None))
    with pytest.raises(ToolError) as exc:
        await client.get_issue("SUP-1")
    assert exc.value.rule == "missing_credentials"
    assert seen == []


def test_base_url_must_be_https():
    with pytest.raises(ValueError, match="https"):
        _settings(jira_base_url="http://acme.atlassian.net")
