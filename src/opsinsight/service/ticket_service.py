import json
from collections.abc import Mapping
from typing import Any

import httpx

from opsinsight.core.config import Settings
from opsinsight.core.errors import ToolError
from opsinsight.core.limits import fit_rows_to_budget, truncate_cell
from opsinsight.core.redaction import redact_row, redact_text
from opsinsight.integrations.tickets.guardrail import (
    clamp_limit,
    select_provider,
    validate_search,
    validate_ticket_id,
)
from opsinsight.integrations.tickets.jira_client import JiraClient
from opsinsight.integrations.tickets.linear_client import LinearClient
from opsinsight.service.base import BaseService
from opsinsight.service.ports import TicketProvider, TicketProviderFactory

DEFAULT_PROVIDERS: dict[str, TicketProviderFactory] = {"linear": LinearClient, "jira": JiraClient}

# Ticket text is written by customers and staff; it can contain text aimed at
# the model ("ignore your rules..."). Every response says so in `meta`.
CONTENT_TRUST = "untrusted_user_content"

DEFAULT_SEARCH_LIMIT = 20


class TicketService(BaseService):
    """Business logic for the ticket tools (Linear / Jira), read-only.

    Providers are constructor-injected as factories keyed by provider name,
    so tests pass fakes instead of monkeypatching. Responses are never cached:
    tickets change constantly, and a stale status would mislead an
    investigation."""

    def __init__(self, settings: Settings, providers: Mapping[str, TicketProviderFactory] | None = None) -> None:
        super().__init__(settings)
        self._factories = dict(providers if providers is not None else DEFAULT_PROVIDERS)
        self._instances: dict[str, TicketProvider] = {}

    def _provider(self, name: str) -> TicketProvider:
        if name not in self._instances:
            factory = self._factories.get(name)
            if factory is None:
                raise ToolError(rule="invalid_provider", message=f"No {name} provider is available.")
            self._instances[name] = factory(self.settings)
        return self._instances[name]

    @staticmethod
    def _httpx_error_response(e: httpx.HTTPError, provider: str) -> dict:
        label = provider.capitalize()
        if isinstance(e, httpx.HTTPStatusError):
            status = e.response.status_code
            detail = redact_text(e.response.text[:500])
            if status in (401, 403):
                rule, message = "auth_failed", f"{label} rejected the credentials (HTTP {status})."
            elif status == 404:
                rule, message = "ticket_not_found", f"{label} has no such issue, or these credentials can't see it."
                detail = None
            elif status == 429:
                retry = e.response.headers.get("Retry-After")
                rule = "rate_limited"
                message = f"{label} rate limit reached" + (f"; retry after {retry}s." if retry else "; retry later.")
            elif status == 400:
                rule, message = "invalid_query", f"{label} rejected the request (HTTP 400). Check the JQL / id."
            else:
                rule, message = "ticket_http_error", f"{label} returned HTTP {status}."
            return ToolError(rule=rule, message=message, detail=detail).to_response()
        if isinstance(e, httpx.TimeoutException):
            return ToolError(
                rule="upstream_timeout",
                message=f"{label} did not answer in time.",
                detail="Retry, or raise TICKET_HTTP_TIMEOUT_S.",
            ).to_response()
        return ToolError(
            rule="ticket_connection_error", message=f"Could not reach {label}.", detail=redact_text(str(e))
        ).to_response()

    def _redact(self, value: dict[str, Any]) -> dict[str, Any]:
        return redact_row(value, self.settings)

    def _cap_text(self, value: str) -> tuple[str, bool]:
        capped = truncate_cell(value, self.settings.ticket_max_description_bytes)
        return str(capped), capped != value

    def _cap_comments(self, comments: list[dict[str, Any]], budget: int) -> tuple[list[dict[str, Any]], bool]:
        """Redacted comments → per-body cap → drop trailing ones past `budget`."""
        truncated = False
        capped = []
        for comment in comments:
            body, cut = self._cap_text(comment.get("body") or "")
            truncated = truncated or cut
            capped.append({**comment, "body": body})
        kept, dropped, _ = fit_rows_to_budget(capped, max(0, budget))
        return kept, truncated or dropped

    def _meta(self, provider: str, **extra: Any) -> dict[str, Any]:
        return {"provider": provider, "content_trust": CONTENT_TRUST, **extra}

    async def get_ticket(self, ticket_id: str, provider: str | None = None, include_comments: bool = False) -> dict:
        settings = self.settings
        name = provider or "auto"
        try:
            name = select_provider(provider, settings)
            key = validate_ticket_id(ticket_id, name)
            client = self._provider(name)
            ticket = (await client.get_issue(key)).to_dict()
            page = await client.list_comments(key, settings.ticket_max_results) if include_comments else None
        except ToolError as e:
            return e.to_response()
        except httpx.HTTPError as e:
            return self._httpx_error_response(e, name)

        # Redact first, then truncate, so a cut can't hide part of a secret.
        ticket = self._redact(ticket)
        ticket["description"], description_cut = self._cap_text(ticket.get("description") or "")
        truncated = description_cut
        meta = self._meta(name)

        if page is not None:
            comments = [self._redact(c.to_dict()) for c in page.comments]
            used = len(json.dumps(ticket, default=str).encode("utf-8"))
            kept, cut = self._cap_comments(comments, settings.ticket_max_response_bytes - used)
            ticket["comments"] = kept
            truncated = truncated or cut
            meta.update(
                comments_returned=len(kept),
                comments_total=page.total,
                comments_has_more=page.has_more or len(kept) < len(comments),
            )
        meta["truncated"] = truncated
        return self.ok(ticket, meta)

    async def search_tickets(
        self,
        query: str | None = None,
        provider: str | None = None,
        limit: int | None = DEFAULT_SEARCH_LIMIT,
        jql: str | None = None,
    ) -> dict:
        settings = self.settings
        name = provider or "auto"
        try:
            name = select_provider(provider, settings)
            spec = validate_search(query, jql, name)
            effective, clamped = clamp_limit(limit, settings.ticket_max_results, DEFAULT_SEARCH_LIMIT)
            hits = await self._provider(name).search_issues(spec.text, spec.jql, effective)
        except ToolError as e:
            return e.to_response()
        except httpx.HTTPError as e:
            return self._httpx_error_response(e, name)

        rows = [self._redact(hit.to_dict()) for hit in hits]
        kept, truncated, _ = fit_rows_to_budget(rows, settings.ticket_max_response_bytes)
        note = None
        if not kept:
            note = "No matching tickets. Try fewer or different words" + (
                ", or a broader JQL." if name == "jira" else "."
            )
        return self.ok(
            {"tickets": kept, "note": note},
            self._meta(name, row_count=len(kept), limit=effective, limit_clamped=clamped, truncated=truncated),
        )

    async def list_comments(self, ticket_id: str, provider: str | None = None, limit: int | None = None) -> dict:
        settings = self.settings
        name = provider or "auto"
        try:
            name = select_provider(provider, settings)
            key = validate_ticket_id(ticket_id, name)
            effective, clamped = clamp_limit(limit, settings.ticket_max_results, settings.ticket_max_results)
            page = await self._provider(name).list_comments(key, effective)
        except ToolError as e:
            return e.to_response()
        except httpx.HTTPError as e:
            return self._httpx_error_response(e, name)

        comments = [self._redact(c.to_dict()) for c in page.comments]
        kept, truncated = self._cap_comments(comments, settings.ticket_max_response_bytes)
        return self.ok(
            {"ticket_id": key, "comments": kept},
            self._meta(
                name,
                row_count=len(kept),
                comments_total=page.total,
                comments_has_more=page.has_more or len(kept) < len(comments),
                limit=effective,
                limit_clamped=clamped,
                truncated=truncated,
            ),
        )
