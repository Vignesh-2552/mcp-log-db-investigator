import re
from datetime import UTC, datetime, timedelta

from opsinsight.core.errors import NewRelicGuardrailError
from opsinsight.integrations.newrelic.constants import (
    ATTRIBUTE_NAME_RE,
    BLOCKED_KEYWORDS,
    EPOCH_MS_RE,
    EPOCH_S_RE,
    EVENT_TYPE_RE,
    LIMIT_RE,
    LITERAL_OR_COMMENT_RE,
    MAX_NRQL_CHARS,
    RELATIVE_TIME_RE,
    SELECT_KEYWORD_RE,
    SELECT_RE,
    SINCE_RE,
    UNIT_HOURS,
    UNTIL_RE,
)


def _mask_literals_and_comments(query: str) -> tuple[str, bool]:
    """Returns (masked, has_line_comment). `masked` has every string literal
    and comment replaced by same-length filler, so regex checks never fire on
    user text inside them and match offsets stay valid on the original query."""
    has_line_comment = False

    def _mask(match: re.Match[str]) -> str:
        nonlocal has_line_comment
        if match.group().startswith(("--", "//")):
            has_line_comment = True
        return "x" * len(match.group())

    return LITERAL_OR_COMMENT_RE.sub(_mask, query), has_line_comment


def _enclosing_groups(masked: str) -> list[int]:
    """For each offset, the offset of the innermost unclosed `(` before it, or
    -1 at the top level. Run on the masked query so parentheses inside
    literals and comments don't count. Clauses in the same group belong to
    the same (sub)query."""
    stack: list[int] = []
    groups: list[int] = []
    for i, ch in enumerate(masked):
        if ch == ")" and stack:
            stack.pop()
        groups.append(stack[-1] if stack else -1)
        if ch == "(":
            stack.append(i)
    return groups


def _append_clause(query: str, clause: str, has_line_comment: bool) -> str:
    # A trailing `--`/`//` comment would swallow a clause appended on the same line.
    separator = "\n" if has_line_comment else " "
    return f"{query}{separator}{clause}"


def validate_event_type(event_type: str) -> str:
    if not EVENT_TYPE_RE.match(event_type):
        raise NewRelicGuardrailError(
            rule="invalid_event_type",
            message="event_type must be a bare identifier (letters, digits, underscore).",
            detail=f"Got: {event_type!r}",
        )
    return event_type


def validate_attribute_name(attribute: str) -> str:
    if not ATTRIBUTE_NAME_RE.match(attribute):
        raise NewRelicGuardrailError(
            rule="invalid_field_name",
            message="field must be an attribute name (letters, digits, underscore, dot).",
            detail=f"Got: {attribute!r}",
        )
    return attribute


def build_nrql_string_literal(value: str) -> str:
    """Escapes `value` for embedding inside a single-quoted NRQL literal.
    Backslash-escapes backslashes and single quotes, and rejects newlines
    outright so a value can't break out of the literal and append clauses."""
    if "\n" in value or "\r" in value:
        raise NewRelicGuardrailError(
            rule="invalid_trace_value",
            message="value must not contain newlines.",
            detail=f"Got: {value!r}",
        )
    return value.replace("\\", "\\\\").replace("'", "\\'")


def validate_nrql(query: str, max_rows: int, requested_limit: int | None = None) -> str:
    """Raises NewRelicGuardrailError with a specific `rule` on the first
    violation found. Returns the query with LIMIT injected/clamped."""
    stripped = query.strip()
    if not stripped:
        raise NewRelicGuardrailError(rule="empty_query", message="NRQL query must not be empty.")
    if len(stripped) > MAX_NRQL_CHARS:
        raise NewRelicGuardrailError(
            rule="query_too_long",
            message=f"NRQL is {len(stripped)} characters; the maximum is {MAX_NRQL_CHARS}.",
        )

    masked, has_line_comment = _mask_literals_and_comments(stripped)
    if masked.endswith(";"):
        stripped = stripped[:-1].rstrip()
        masked = masked[: len(stripped)]
    if ";" in masked:
        raise NewRelicGuardrailError(
            rule="multiple_statements",
            message="Only a single NRQL statement is allowed.",
        )

    if not SELECT_RE.match(masked) or not SELECT_KEYWORD_RE.search(masked):
        raise NewRelicGuardrailError(
            rule="not_select",
            message="Only SELECT NRQL queries are permitted.",
            detail=f"Query started with: {stripped[:30]!r}",
        )

    upper = masked.upper()
    for keyword in BLOCKED_KEYWORDS:
        if re.search(rf"\b{keyword}\b", upper):
            raise NewRelicGuardrailError(
                rule="blocked_keyword",
                message=f"Blocked keyword in query: {keyword}.",
                detail="Keywords inside quoted strings are allowed. If this is an attribute name, "
                f"wrap it in backticks (`{keyword.lower()}`).",
            )

    # Every LIMIT is clamped, not just the first one found: a subquery's
    # LIMIT can't stand in for the outer one, and `LIMIT MAX` is never kept.
    target = min(requested_limit, max_rows) if requested_limit is not None else max_rows
    groups = _enclosing_groups(masked)
    pieces: list[str] = []
    cursor = 0
    has_top_level_limit = False
    for match in LIMIT_RE.finditer(masked):
        existing = match.group(1)
        top_level = groups[match.start()] == -1
        cap = target if top_level else max_rows
        value = min(cap, int(existing)) if existing.isdigit() else cap
        has_top_level_limit = has_top_level_limit or top_level
        pieces.append(f"{stripped[cursor: match.start()]}LIMIT {value}")
        cursor = match.end()
    result = "".join(pieces) + stripped[cursor:]
    if has_top_level_limit:
        return result
    return _append_clause(result, f"LIMIT {target}", has_line_comment)


def _parse_time_point(operand: str, now: datetime) -> datetime | None:
    """Parses one SINCE/UNTIL operand into an absolute UTC datetime, or None if
    the form isn't supported. Naive dates are read as UTC (New Relic's default
    unless the query says WITH TIMEZONE)."""
    text = operand.strip()
    lowered = text.lower()
    if lowered == "now":
        return now
    if lowered in ("today", "yesterday"):
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return midnight if lowered == "today" else midnight - timedelta(days=1)
    relative = RELATIVE_TIME_RE.match(text)
    if relative:
        return now - timedelta(hours=int(relative.group(1)) * UNIT_HOURS[relative.group(2).lower()])
    if EPOCH_MS_RE.match(text):
        return datetime.fromtimestamp(int(text) / 1000, UTC)
    if EPOCH_S_RE.match(text):
        return datetime.fromtimestamp(int(text), UTC)
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        try:
            parsed = datetime.fromisoformat(text[1:-1])
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def _check_span(since_operand: str, until_operand: str, now: datetime, max_hours: int) -> None:
    since_dt = _parse_time_point(since_operand, now)
    until_dt = _parse_time_point(until_operand, now)
    unsupported = [op for op, dt in ((since_operand, since_dt), (until_operand, until_dt)) if dt is None]
    if unsupported:
        raise NewRelicGuardrailError(
            rule="unsupported_time_range",
            message=f"Could not determine the time window from: {unsupported[0].strip()!r}.",
            detail=(
                "Use `SINCE <n> minutes|hours|days ago`, `SINCE today|yesterday`, "
                "epoch milliseconds, or a quoted date like `SINCE '2026-10-28 14:00:00'`."
            ),
        )
    if since_dt >= until_dt:
        raise NewRelicGuardrailError(rule="invalid_window", message="SINCE must be before UNTIL.")
    span_hours = (until_dt - since_dt).total_seconds() / 3600
    if span_hours > max_hours:
        raise NewRelicGuardrailError(
            rule="window_too_large",
            message=f"Requested window ({span_hours:.1f}h) exceeds the max of {max_hours}h.",
            detail=f"Narrow SINCE/UNTIL to at most {max_hours} hours.",
        )


def enforce_nrql_window(
    query: str,
    default_hours: int,
    max_hours: int,
    now: datetime | None = None,
) -> str:
    """Caps every (sub)query's SINCE/UNTIL span at `max_hours`, injecting
    `SINCE <default_hours> hours ago` at the top level when the outer query
    has no SINCE. A subquery without SINCE inherits the outer window. Accepts
    relative (`SINCE 3 hours ago`), named (`now`, `today`, `yesterday`),
    epoch (seconds or millis) and quoted ISO-date operands; anything else is
    rejected so an unbounded or unparseable range can't slip through. Run on
    `validate_nrql`'s output (trailing `;` already stripped)."""
    now = now or datetime.now(UTC)
    masked, has_line_comment = _mask_literals_and_comments(query)
    groups = _enclosing_groups(masked)

    def first_per_group(pattern: re.Pattern[str]) -> dict[int, re.Match[str]]:
        found: dict[int, re.Match[str]] = {}
        for match in pattern.finditer(masked):
            found.setdefault(groups[match.start()], match)
        return found

    sinces = first_per_group(SINCE_RE)
    untils = first_per_group(UNTIL_RE)

    for group, until_match in untils.items():
        if group not in sinces:
            raise NewRelicGuardrailError(
                rule="until_without_since",
                message="UNTIL requires a SINCE clause in the same (sub)query.",
                detail=f"Add e.g. `SINCE {default_hours} hours ago` before UNTIL.",
            )
    for group, since_match in sinces.items():
        since_operand = query[since_match.start(1) : since_match.end(1)]
        until_match = untils.get(group)
        until_operand = query[until_match.start(1) : until_match.end(1)] if until_match else "now"
        _check_span(since_operand, until_operand, now, max_hours)

    if -1 in sinces:
        return query
    clause = f"SINCE {default_hours} hours ago"
    top_limit = next((m for m in LIMIT_RE.finditer(masked) if groups[m.start()] == -1), None)
    if top_limit:
        return f"{query[: top_limit.start()]}{clause} {query[top_limit.start():]}"
    return _append_clause(query, clause, has_line_comment)
