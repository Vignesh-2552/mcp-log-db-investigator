import re

# NRQL doesn't have a widely-available AST parser (unlike SQL's sqlglot), so
# these back a regex-based first line of defence: reject anything that isn't
# a single SELECT, reject obvious write-ish keywords, clamp/inject LIMIT and
# cap the SINCE/UNTIL window. Not as rigorous as the SQL guardrail — good
# enough for read-only NRQL against Log/Metric/event data, which is what the
# `nrql` GraphQL field accepts in the first place (mutations like alert
# creation go through entirely different GraphQL operations, not this field).
# Every regex below runs against the query with string literals and comments
# masked out (see guardrail._mask_literals_and_comments), so text inside
# `WHERE message LIKE '%update;%'` can't trip a check.

SELECT_RE = re.compile(r"^\s*SELECT\s", re.IGNORECASE)
LIMIT_RE = re.compile(r"\bLIMIT\s+(\d+|MAX)\b", re.IGNORECASE)

BLOCKED_KEYWORDS = ("DELETE", "INSERT", "UPDATE", "CREATE", "DROP", "ALTER", "GRANT", "TRUNCATE")

EVENT_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Longest NRQL accepted. Bounds the regex passes below on hostile input.
MAX_NRQL_CHARS = 20_000

# String literals ('...', "...", `...` with backslash escapes) and comments
# (--, //, /* */).
LITERAL_OR_COMMENT_RE = re.compile(
    r"'(?:\\.|[^'\\])*'"
    r'|"(?:\\.|[^"\\])*"'
    r"|`[^`]*`"
    r"|--[^\n]*"
    r"|//[^\n]*"
    r"|/\*.*?(?:\*/|$)",
    re.DOTALL,
)

# SINCE/UNTIL operand: everything up to the next clause keyword.
_CLAUSE_END = (
    r"(?=\s+(?:UNTIL|SINCE|LIMIT|FACET|TIMESERIES|WHERE|COMPARE|WITH|EXTRAPOLATE|"
    r"ORDER|SLIDE|AS|OFFSET|RAW)\b|\s*$)"
)
SINCE_RE = re.compile(rf"\bSINCE\s+(.+?){_CLAUSE_END}", re.IGNORECASE | re.DOTALL)
UNTIL_RE = re.compile(rf"\bUNTIL\s+(.+?){_CLAUSE_END}", re.IGNORECASE | re.DOTALL)

RELATIVE_TIME_RE = re.compile(
    r"^(\d+)\s+(second|minute|hour|day|week|month)s?\s+ago$", re.IGNORECASE
)
EPOCH_MS_RE = re.compile(r"^\d{13}$")
EPOCH_S_RE = re.compile(r"^\d{10}$")

UNIT_HOURS = {
    "second": 1 / 3600,
    "minute": 1 / 60,
    "hour": 1,
    "day": 24,
    "week": 24 * 7,
    "month": 24 * 30,
}
