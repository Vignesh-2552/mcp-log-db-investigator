import re

from investigation_server.core.errors import NewRelicGuardrailError

# NRQL doesn't have a widely-available AST parser (unlike SQL's sqlglot), so
# this is a regex-based first line of defence: reject anything that isn't a
# single SELECT, reject obvious write-ish keywords, and clamp/inject LIMIT.
# Not as rigorous as the SQL guardrail — good enough for read-only NRQL
# against Log/Metric/event data, which is what the `nrql` GraphQL field
# accepts in the first place (mutations like alert creation go through
# entirely different GraphQL operations, not this field).

_SELECT_RE = re.compile(r"^\s*SELECT\s", re.IGNORECASE)
_LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)

BLOCKED_KEYWORDS = ("DELETE", "INSERT", "UPDATE", "CREATE", "DROP", "ALTER", "GRANT", "TRUNCATE")


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

    if not _SELECT_RE.match(stripped):
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
    match = _LIMIT_RE.search(stripped)
    if match:
        existing_limit = int(re.search(r"\d+", match.group()).group())
        target = min(target, existing_limit)
        stripped = _LIMIT_RE.sub(f"LIMIT {target}", stripped, count=1)
    else:
        stripped = f"{stripped} LIMIT {target}"

    return stripped
