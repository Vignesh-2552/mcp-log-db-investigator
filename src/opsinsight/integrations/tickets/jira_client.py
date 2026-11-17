"""Jira Cloud REST v3 I/O (read-only). Returns the provider-agnostic shapes in
`models.py`; ADF bodies are flattened to text with `adf.py`. Ids, JQL and limits
arrive already checked by `guardrail.py`; HTTP errors propagate as
`httpx.HTTPError` for the service to translate (404 → `ticket_not_found`)."""

from typing import Any
from urllib.parse import quote

import httpx

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.logging_config import get_logger
from opsinsight.integrations.tickets.adf import adf_to_text
from opsinsight.integrations.tickets.guardrail import SearchSpec, build_jira_jql
from opsinsight.integrations.tickets.models import (
    CommentPage,
    Ticket,
    TicketComment,
    TicketPerson,
    TicketSearchHit,
)

logger = get_logger("tickets.jira")

ISSUE_FIELDS = "summary,description,status,assignee,reporter,created,updated,priority,labels,issuetype,project"
SEARCH_FIELDS = ["summary", "status", "updated", "assignee"]


def _person(raw: dict | None) -> TicketPerson | None:
    # emailAddress is deliberately not copied.
    if not raw:
        return None
    return TicketPerson(display_name=raw.get("displayName"), account_id=raw.get("accountId"))


def _name(raw: dict | None) -> str | None:
    return (raw or {}).get("name")


class JiraClient:
    """Implements `service.ports.TicketProvider` against Jira Cloud."""

    name = "jira"

    def __init__(self, settings: Settings, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._settings = settings
        self._transport = transport

    def _base(self) -> str:
        return (self._settings.jira_base_url or "").rstrip("/")

    async def _request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        settings = self._settings
        if not settings.jira_configured or settings.jira_api_token is None or settings.jira_email is None:
            raise ToolError(rule="missing_credentials", message="Jira is not configured.")
        async with httpx.AsyncClient(
            base_url=self._base(),
            timeout=settings.ticket_http_timeout_s,
            transport=self._transport,
            auth=httpx.BasicAuth(settings.jira_email, settings.jira_api_token.get_secret_value()),
            headers={"Accept": "application/json"},
        ) as client:
            response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def _browse_url(self, key: str) -> str:
        return f"{self._base()}/browse/{key}"

    async def get_issue(self, ticket_id: str) -> Ticket:
        raw = await self._request(
            "GET", f"/rest/api/3/issue/{quote(ticket_id, safe='')}", params={"fields": ISSUE_FIELDS}
        )
        fields = raw.get("fields") or {}
        key = raw.get("key") or ticket_id
        return Ticket(
            provider=self.name,
            id=str(raw.get("id") or ticket_id),
            key=key,
            title=fields.get("summary") or "",
            description=adf_to_text(fields.get("description")),
            status=_name(fields.get("status")),
            url=self._browse_url(key),
            created_at=fields.get("created"),
            updated_at=fields.get("updated"),
            assignee=_person(fields.get("assignee")),
            reporter=_person(fields.get("reporter")),
            priority=_name(fields.get("priority")),
            labels=[label for label in fields.get("labels") or [] if isinstance(label, str)],
            project=(fields.get("project") or {}).get("key"),
            issue_type=_name(fields.get("issuetype")),
        )

    async def search_issues(self, text: str | None, jql: str | None, limit: int) -> list[TicketSearchHit]:
        # /rest/api/3/search is deprecated; /search/jql is the replacement
        # (token paging, no `total`). One page is enough: limit <= 100.
        query = build_jira_jql(SearchSpec(text=text, jql=jql))
        raw = await self._request(
            "POST",
            "/rest/api/3/search/jql",
            json={"jql": query, "maxResults": limit, "fields": SEARCH_FIELDS},
        )
        hits = []
        for issue in (raw.get("issues") or [])[:limit]:
            fields = issue.get("fields") or {}
            key = issue.get("key") or str(issue.get("id"))
            hits.append(
                TicketSearchHit(
                    provider=self.name,
                    id=str(issue.get("id") or key),
                    key=key,
                    title=fields.get("summary") or "",
                    status=_name(fields.get("status")),
                    url=self._browse_url(key),
                    updated_at=fields.get("updated"),
                    assignee=_person(fields.get("assignee")),
                )
            )
        return hits

    async def list_comments(self, ticket_id: str, limit: int) -> CommentPage:
        raw = await self._request(
            "GET",
            f"/rest/api/3/issue/{quote(ticket_id, safe='')}/comment",
            params={"maxResults": limit, "orderBy": "created"},
        )
        comments = [
            TicketComment(
                id=str(item.get("id")),
                author=_person(item.get("author")),
                body=adf_to_text(item.get("body")),
                created_at=item.get("created"),
                updated_at=item.get("updated"),
            )
            for item in (raw.get("comments") or [])[:limit]
        ]
        total = raw.get("total")
        total = int(total) if isinstance(total, int) else None
        has_more = total is not None and total > len(comments)
        return CommentPage(comments=comments, total=total, has_more=has_more)
