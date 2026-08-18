import re

# NRQL doesn't have a widely-available AST parser (unlike SQL's sqlglot), so
# these back a regex-based first line of defence: reject anything that isn't
# a single SELECT, reject obvious write-ish keywords, and clamp/inject LIMIT.
# Not as rigorous as the SQL guardrail — good enough for read-only NRQL
# against Log/Metric/event data, which is what the `nrql` GraphQL field
# accepts in the first place (mutations like alert creation go through
# entirely different GraphQL operations, not this field).

SELECT_RE = re.compile(r"^\s*SELECT\s", re.IGNORECASE)
LIMIT_RE = re.compile(r"\bLIMIT\s+\d+\b", re.IGNORECASE)

BLOCKED_KEYWORDS = ("DELETE", "INSERT", "UPDATE", "CREATE", "DROP", "ALTER", "GRANT", "TRUNCATE")

EVENT_TYPE_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
