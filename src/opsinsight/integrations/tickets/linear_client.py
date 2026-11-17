"""Linear GraphQL I/O (read-only). Returns the provider-agnostic shapes in
`models.py`. No validation here: ids and limits arrive already checked by
`guardrail.py`; HTTP errors propagate as `httpx.HTTPError` for the service to
translate."""

from typing import Any

import httpx

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.logging_config import get_logger
from opsinsight.core.redaction import redact_text
from opsinsight.integrations.tickets.models import (
    CommentPage,
    Ticket,
    TicketComment,
    TicketPerson,
    TicketSearchHit,
)

logger = get_logger("tickets.linear")

LINEAR_GRAPHQL_URL = "https://api.linear.app/graphql"

_PERSON = "{ id displayName name }"

ISSUE_QUERY = f"""
query Issue($id: String!) {{
  issue(id: $id) {{
    id identifier title description url createdAt updatedAt priorityLabel
    state {{ name }}
    assignee {_PERSON}
    creator {_PERSON}
    team {{ key }}
    labels {{ nodes {{ name }} }}
  }}
}}
"""

SEARCH_QUERY = f"""
query Search($term: String!, $first: Int!) {{
  searchIssues(term: $term, first: $first) {{
    nodes {{
      id identifier title url updatedAt
      state {{ name }}
      assignee {_PERSON}
    }}
  }}
}}
"""

COMMENTS_QUERY = f"""
query Comments($id: String!, $first: Int!) {{
  issue(id: $id) {{
    comments(first: $first) {{
      nodes {{ id body createdAt updatedAt user {_PERSON} }}
      pageInfo {{ hasNextPage }}
    }}
  }}
}}
"""


def _person(raw: dict | None) -> TicketPerson | None:
    if not raw:
        return None
    return TicketPerson(display_name=raw.get("displayName") or raw.get("name"), account_id=raw.get("id"))


def _graphql_error(errors: list[dict]) -> ToolError:
    first = errors[0] if errors else {}
    message = str(first.get("message") or "")
    code = str(((first.get("extensions") or {}).get("code")) or "").upper()
    detail = redact_text(str(errors)[:500])
    if code == "RATELIMITED" or "rate limit" in message.lower():
        return ToolError(rule="rate_limited", message="Linear rate limit reached; retry later.", detail=detail)
    if code in ("AUTHENTICATION_ERROR", "FORBIDDEN") or "authentication" in message.lower():
        return ToolError(rule="auth_failed", message="Linear rejected the API key.", detail=detail)
    if "not found" in message.lower():
        return ToolError(rule="ticket_not_found", message="Linear has no such issue (or the key can't see it).")
    return ToolError(rule="ticket_api_error", message="Linear API returned an error.", detail=detail)


class LinearClient:
    """Implements `service.ports.TicketProvider` against Linear."""

    name = "linear"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    async def _query(self, query: str, variables: dict[str, Any]) -> dict[str, Any]:
        key = self._settings.linear_api_key
        if key is None:  # guarded by select_provider; defensive
            raise ToolError(rule="missing_credentials", message="LINEAR_API_KEY is not set.")
        async with httpx.AsyncClient(
            timeout=self._settings.ticket_http_timeout_s, transport=self._transport
        ) as client:
            response = await client.post(
                LINEAR_GRAPHQL_URL,
                # Personal API keys go in the header as-is (OAuth tokens use "Bearer").
                headers={"Authorization": key.get_secret_value(), "Content-Type": "application/json"},
                json={"query": query, "variables": variables},
            )
        if response.status_code == 400:
            # Linear reports GraphQL errors (incl. rate limiting) as HTTP 400 with a body.
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            if payload.get("errors"):
                raise _graphql_error(payload["errors"])
        response.raise_for_status()
        payload = response.json()
        if payload.get("errors"):
            raise _graphql_error(payload["errors"])
        return payload.get("data") or {}

    async def get_issue(self, ticket_id: str) -> Ticket:
        data = await self._query(ISSUE_QUERY, {"id": ticket_id})
        issue = data.get("issue")
        if not issue:
            raise ToolError(rule="ticket_not_found", message=f"Linear has no issue {ticket_id}.")
        return Ticket(
            provider=self.name,
            id=issue["id"],
            key=issue.get("identifier") or issue["id"],
            title=issue.get("title") or "",
            description=issue.get("description") or "",
            status=(issue.get("state") or {}).get("name"),
            url=issue.get("url"),
            created_at=issue.get("createdAt"),
            updated_at=issue.get("updatedAt"),
            assignee=_person(issue.get("assignee")),
            reporter=_person(issue.get("creator")),
            priority=issue.get("priorityLabel"),
            labels=[n.get("name") for n in (issue.get("labels") or {}).get("nodes") or [] if n.get("name")],
            project=(issue.get("team") or {}).get("key"),
        )

    async def search_issues(self, text: str | None, jql: str | None, limit: int) -> list[TicketSearchHit]:
        del jql  # Jira-only; the guardrail rejects `jql` for Linear before this point.
        data = await self._query(SEARCH_QUERY, {"term": text or "", "first": limit})
        nodes = (data.get("searchIssues") or {}).get("nodes") or []
        return [
            TicketSearchHit(
                provider=self.name,
                id=node["id"],
                key=node.get("identifier") or node["id"],
                title=node.get("title") or "",
                status=(node.get("state") or {}).get("name"),
                url=node.get("url"),
                updated_at=node.get("updatedAt"),
                assignee=_person(node.get("assignee")),
            )
            for node in nodes[:limit]
        ]

    async def list_comments(self, ticket_id: str, limit: int) -> CommentPage:
        data = await self._query(COMMENTS_QUERY, {"id": ticket_id, "first": limit})
        issue = data.get("issue")
        if not issue:
            raise ToolError(rule="ticket_not_found", message=f"Linear has no issue {ticket_id}.")
        connection = issue.get("comments") or {}
        comments = [
            TicketComment(
                id=node["id"],
                author=_person(node.get("user")),
                body=node.get("body") or "",
                created_at=node.get("createdAt"),
                updated_at=node.get("updatedAt"),
            )
            for node in (connection.get("nodes") or [])[:limit]
        ]
        has_more = bool((connection.get("pageInfo") or {}).get("hasNextPage"))
        return CommentPage(comments=comments, total=None, has_more=has_more)
