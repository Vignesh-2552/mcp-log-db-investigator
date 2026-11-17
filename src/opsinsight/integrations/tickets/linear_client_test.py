import json

import httpx
import pytest
from pydantic import SecretStr

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.integrations.tickets.linear_client import (
    LINEAR_GRAPHQL_URL,
    LinearClient,
)


def _client(handler) -> tuple[LinearClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []

    def _record(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return handler(request)

    settings = Settings(_env_file=None, linear_api_key=SecretStr("lin_api_secret"))
    return LinearClient(settings, transport=httpx.MockTransport(_record)), seen


ISSUE = {
    "id": "uuid-1",
    "identifier": "ENG-12",
    "title": "Checkout fails",
    "description": "Customer a@b.test can't pay",
    "url": "https://linear.app/acme/issue/ENG-12",
    "createdAt": "2026-11-01T10:00:00Z",
    "updatedAt": "2026-11-02T10:00:00Z",
    "priorityLabel": "High",
    "state": {"name": "In Progress"},
    "assignee": {"id": "u1", "displayName": "asha", "name": "Asha R", "email": "asha@acme.test"},
    "creator": None,
    "team": {"key": "ENG"},
    "labels": {"nodes": [{"name": "bug"}, {"name": None}]},
}


async def test_get_issue_sends_the_key_and_normalizes():
    client, seen = _client(lambda r: httpx.Response(200, json={"data": {"issue": ISSUE}}))

    ticket = await client.get_issue("ENG-12")

    [request] = seen
    assert str(request.url) == LINEAR_GRAPHQL_URL
    assert request.headers["Authorization"] == "lin_api_secret"  # personal key: no "Bearer"
    assert json.loads(request.content)["variables"] == {"id": "ENG-12"}
    assert ticket.key == "ENG-12"
    assert ticket.status == "In Progress"
    assert ticket.labels == ["bug"]
    assert ticket.project == "ENG"
    assert ticket.reporter is None
    assert ticket.assignee.to_dict() == {"display_name": "asha", "account_id": "u1"}  # no email


async def test_missing_issue_is_ticket_not_found():
    client, _ = _client(lambda r: httpx.Response(200, json={"data": {"issue": None}}))
    with pytest.raises(ToolError) as exc:
        await client.get_issue("ENG-404")
    assert exc.value.rule == "ticket_not_found"


@pytest.mark.parametrize(
    ("status", "errors", "rule"),
    [
        (200, [{"message": "Entity not found: Issue"}], "ticket_not_found"),
        (400, [{"message": "Rate limit exceeded", "extensions": {"code": "RATELIMITED"}}], "rate_limited"),
        (400, [{"message": "Authentication required", "extensions": {"code": "AUTHENTICATION_ERROR"}}],
         "auth_failed"),
        (200, [{"message": "Something odd"}], "ticket_api_error"),
    ],
)
async def test_graphql_errors_map_to_rules(status, errors, rule):
    client, _ = _client(lambda r: httpx.Response(status, json={"errors": errors}))
    with pytest.raises(ToolError) as exc:
        await client.get_issue("ENG-1")
    assert exc.value.rule == rule


async def test_http_errors_propagate_for_the_service_to_translate():
    client, _ = _client(lambda r: httpx.Response(500, text="boom"))
    with pytest.raises(httpx.HTTPStatusError):
        await client.get_issue("ENG-1")


async def test_search_passes_term_and_limit():
    nodes = [{"id": "u", "identifier": "ENG-1", "title": "t", "url": "x", "updatedAt": "d",
              "state": {"name": "Todo"}, "assignee": None}] * 3
    client, seen = _client(lambda r: httpx.Response(200, json={"data": {"searchIssues": {"nodes": nodes}}}))

    hits = await client.search_issues("checkout", None, 2)

    assert json.loads(seen[0].content)["variables"] == {"term": "checkout", "first": 2}
    assert [h.key for h in hits] == ["ENG-1", "ENG-1"]
    assert hits[0].status == "Todo"


async def test_list_comments_reports_has_more():
    data = {"issue": {"comments": {
        "nodes": [{"id": "c1", "body": "hi", "createdAt": "t", "updatedAt": "t", "user": {"id": "u", "name": "N"}}],
        "pageInfo": {"hasNextPage": True},
    }}}
    client, _ = _client(lambda r: httpx.Response(200, json={"data": data}))

    page = await client.list_comments("ENG-1", 1)

    assert page.has_more is True
    assert page.total is None
    assert page.comments[0].author.display_name == "N"
