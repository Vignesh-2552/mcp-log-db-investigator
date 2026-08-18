import re

from core.errors import NewRelicGuardrailError
from integrations.newrelic.constants import BLOCKED_KEYWORDS, LIMIT_RE, SELECT_RE


def validate_nrql(query: str, max_rows: int, requested_limit: int | None = None) -> str:
    """Raises NewRelicGuardrailError with a specific `rule` on the first
    violation found. Returns the query with LIMIT injected/clamped."""
    stripped = query.strip()
    if not stripped:
        raise NewRelicGuardrailError(rule="empty_query", message="NRQL query must not be empty.")

    if stripped.endswith(";"):
        stripped = stripped[:-1].rstrip()
    if ";" in stripped:
        raise NewRelicGuardrailError(
            rule="multiple_statements",
            message="Only a single NRQL statement is allowed.",
        )

    if not SELECT_RE.match(stripped):
        raise NewRelicGuardrailError(
            rule="not_select",
            message="Only SELECT NRQL queries are permitted.",
            detail=f"Query started with: {stripped[:30]!r}",
        )

    upper = stripped.upper()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            raise NewRelicGuardrailError(
                rule="blocked_keyword",
                message=f"Blocked keyword in query: {keyword}.",
            )

    target = min(requested_limit, max_rows) if requested_limit is not None else max_rows
    match = LIMIT_RE.search(stripped)
    if match:
        existing_limit = int(re.search(r"\d+", match.group()).group())
        target = min(target, existing_limit)
        stripped = LIMIT_RE.sub(f"LIMIT {target}", stripped, count=1)
    else:
        stripped = f"{stripped} LIMIT {target}"

    return stripped
