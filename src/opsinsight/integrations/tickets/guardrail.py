"""Pure validation for the ticket tools: provider selection, ticket id format,
search text / JQL checks and limit clamping. No I/O."""

import re
from dataclasses import dataclass

from opsinsight.core.config import Settings
from opsinsight.core.errors import TicketGuardrailError

PROVIDERS = ("linear", "jira")

# ENG-123 / PROJ_X-456 (both providers use team/project key + number).
_ISSUE_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,29}-\d{1,9}$")
_UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")
_JIRA_NUMERIC_ID_RE = re.compile(r"^\d{1,12}$")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_ORDER_BY_RE = re.compile(r"\border\s+by\b", re.IGNORECASE)

MAX_QUERY_CHARS = 200
MAX_JQL_CHARS = 1000


def select_provider(requested: str | None, settings: Settings) -> str:
    """Return the provider a call should use. An explicit `requested` wins;
    otherwise TICKET_PROVIDER, where `auto` means "the one configured"."""
    configured = {"linear": settings.linear_configured, "jira": settings.jira_configured}
    choice = (requested or settings.ticket_provider or "auto").strip().lower()

    if choice not in (*PROVIDERS, "auto"):
        raise TicketGuardrailError(
            rule="invalid_provider",
            message=f"Unknown ticket provider {requested!r}.",
            allowed=list(PROVIDERS),
        )
    if choice == "auto":
        available = [name for name in PROVIDERS if configured[name]]
        if not available:
            raise TicketGuardrailError(
                rule="missing_credentials",
                message="No ticket system is configured.",
                detail="Set LINEAR_API_KEY, or JIRA_BASE_URL + JIRA_EMAIL + JIRA_API_TOKEN.",
            )
        if len(available) > 1:
            raise TicketGuardrailError(
                rule="provider_ambiguous",
                message="Both Linear and Jira are configured; pass `provider`.",
                detail="Ticket ids look the same in both systems (e.g. ENG-123), so the provider can't be inferred.",
                allowed=available,
            )
        return available[0]
    if not configured[choice]:
        needed = "LINEAR_API_KEY" if choice == "linear" else "JIRA_BASE_URL, JIRA_EMAIL and JIRA_API_TOKEN"
        raise TicketGuardrailError(
            rule="missing_credentials",
            message=f"{choice.capitalize()} is not configured.",
            detail=f"Set {needed} (in .env or the MCP client config's env block).",
        )
    return choice


def validate_ticket_id(ticket_id: str, provider: str) -> str:
    """Normalize and check a ticket id. Keys are upper-cased (`eng-12` → `ENG-12`)."""
    value = (ticket_id or "").strip()
    if _ISSUE_KEY_RE.match(value):
        return value.upper()
    if provider == "linear" and _UUID_RE.match(value):
        return value.lower()
    if provider == "jira" and _JIRA_NUMERIC_ID_RE.match(value):
        return value
    accepted = "a key like ENG-123 or an issue UUID" if provider == "linear" else "a key like PROJ-456 or a numeric issue id"
    raise TicketGuardrailError(
        rule="invalid_ticket_id",
        message=f"{value[:60]!r} is not a valid {provider} ticket id.",
        detail=f"Pass {accepted}.",
    )


@dataclass(frozen=True)
class SearchSpec:
    """A validated search: plain `text`, or (Jira only) a raw `jql` string."""

    text: str | None = None
    jql: str | None = None


def validate_search(query: str | None, jql: str | None, provider: str) -> SearchSpec:
    query = (query or "").strip()
    jql = (jql or "").strip()
    if query and jql:
        raise TicketGuardrailError(
            rule="invalid_query", message="Pass either `query` or `jql`, not both."
        )
    if jql:
        if provider != "jira":
            raise TicketGuardrailError(
                rule="invalid_query",
                message="`jql` is only supported for Jira.",
                detail="Use `query` (plain text) for Linear.",
            )
        if len(jql) > MAX_JQL_CHARS:
            raise TicketGuardrailError(
                rule="invalid_query", message=f"`jql` is longer than {MAX_JQL_CHARS} characters."
            )
        if _CONTROL_RE.search(jql):
            raise TicketGuardrailError(rule="invalid_query", message="`jql` contains control characters.")
        return SearchSpec(jql=jql)
    if not query:
        raise TicketGuardrailError(
            rule="invalid_query",
            message="Search text is empty.",
            detail="Pass a few words from the ticket title or description in `query`.",
        )
    if len(query) > MAX_QUERY_CHARS:
        raise TicketGuardrailError(
            rule="invalid_query", message=f"`query` is longer than {MAX_QUERY_CHARS} characters."
        )
    return SearchSpec(text=_CONTROL_RE.sub(" ", query))


def build_jira_jql(spec: SearchSpec) -> str:
    """JQL for a validated search. Plain text becomes an escaped `text ~` clause;
    either form is ordered newest-updated first unless it already orders."""
    if spec.jql is not None:
        jql = spec.jql
    else:
        escaped = (spec.text or "").replace("\\", "\\\\").replace('"', '\\"')
        jql = f'text ~ "{escaped}"'
    if not _ORDER_BY_RE.search(jql):
        jql = f"{jql} ORDER BY updated DESC"
    return jql


def clamp_limit(limit: int | None, maximum: int, default: int) -> tuple[int, bool]:
    """Returns (effective_limit, clamped). Clamping isn't an error: callers
    report it in `meta.limit_clamped`."""
    if limit is None:
        return min(default, maximum), False
    effective = max(1, min(int(limit), maximum))
    return effective, effective != limit
