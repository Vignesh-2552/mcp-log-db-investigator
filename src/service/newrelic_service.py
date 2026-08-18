from collections.abc import Awaitable, Callable

import httpx

from core.config import Settings
from core.errors import ToolError
from core.field_heuristics import CORRELATION_ID_RE as _CORRELATION_ID_RE
from core.redaction import redact_rows
from integrations.newrelic.client import run_nrql
from integrations.newrelic.constants import EVENT_TYPE_RE
from integrations.newrelic.guardrail import validate_nrql
from integrations.newrelic.models import (
    EventTypesResult,
    LogFieldsResult,
    NrqlQueryResult,
)
from integrations.newrelic.queries import (
    build_keyset_query,
    build_show_event_types_query,
)
from service.base import BaseService


class NewRelicService(BaseService):
    """Business logic for every New Relic-backed tool. The NRQL runner is
    constructor-injected (defaulting to the real NerdGraph client) so tests
    can substitute a fake via the constructor instead of monkeypatching
    module globals — dependency inversion instead of patching."""

    def __init__(
        self,
        settings: Settings,
        run_nrql_fn: Callable[[str, Settings], Awaitable[dict]] = run_nrql,
    ) -> None:
        super().__init__(settings)
        self._run_nrql = run_nrql_fn

    @staticmethod
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

    async def describe_log_fields(self, event_type: str = "Log", hours: int = 1) -> dict:
        settings = self.settings
        if not EVENT_TYPE_RE.match(event_type):
            return ToolError(
                rule="invalid_event_type",
                message="event_type must be a bare identifier (letters, digits, underscore).",
                detail=f"Got: {event_type!r}",
            ).to_response()

        window = max(1, min(hours, settings.nr_max_window_hours))
        query = build_keyset_query(event_type, window)
        try:
            validated_query = validate_nrql(query, settings.nr_max_rows)
            result = await self._run_nrql(validated_query, settings)
        except ToolError as e:
            return e.to_response()
        except httpx.HTTPError as e:
            return self._httpx_error_response(e)

        rows = result["results"]
        keys = sorted(rows[0].get("keyset", [])) if rows and isinstance(rows[0], dict) else []
        correlation_candidates = sorted(k for k in keys if _CORRELATION_ID_RE.search(k))
        note = None
        if not keys:
            note = (
                f"No {event_type} events found in the last {window}h — widen `hours` or "
                "confirm the event type name (e.g. Log, Transaction, Span)."
            )

        result_model = LogFieldsResult(
            event_type=event_type,
            window_hours=window,
            fields=keys,
            correlation_id_candidates=correlation_candidates,
            note=note,
        )
        return self.ok(result_model.to_dict(), {"field_count": len(keys)})

    async def list_event_types(self, hours: int = 24) -> dict:
        settings = self.settings
        window = max(1, min(hours, settings.nr_max_window_hours))
        query = build_show_event_types_query(window)
        try:
            result = await self._run_nrql(query, settings)
        except ToolError as e:
            return e.to_response()
        except httpx.HTTPError as e:
            return self._httpx_error_response(e)

        rows = result["results"]
        event_types = sorted(
            {v for row in rows if isinstance(row, dict) for v in row.values() if isinstance(v, str)}
        )
        note = None
        if not event_types:
            note = f"No event types found with data in the last {window}h — widen `hours`."

        result_model = EventTypesResult(window_hours=window, event_types=event_types, note=note)
        return self.ok(result_model.to_dict(), {"event_type_count": len(event_types)})

    async def run_nrql_query(self, query: str, limit: int = 100) -> dict:
        settings = self.settings
        try:
            validated_query = validate_nrql(query, settings.nr_max_rows, requested_limit=limit)
            result = await self._run_nrql(validated_query, settings)
        except ToolError as e:
            return e.to_response()
        except httpx.HTTPError as e:
            return self._httpx_error_response(e)

        rows = result["results"]
        if rows and isinstance(rows[0], dict):
            rows = redact_rows(rows, settings)

        result_model = NrqlQueryResult(
            executed_query=validated_query,
            rows=rows,
            row_count=len(rows),
            metadata=result["metadata"],
        )
        return self.ok(result_model.to_dict(), {"row_count": len(rows)})
