import re

# Poll with backoff, capped at the caller's max_wait_s.
BACKOFF_SCHEDULE: tuple[float, ...] = (0.5, 1, 2, 4, 8, 8, 8, 8, 8, 8, 8, 8)

# Logs Insights query statuses considered "still running" — the single source
# of truth for both the polling loop and any caller that needs to check status.
RUNNING_STATUSES = frozenset({"Running", "Scheduled"})

TRACE_FIELD_RE = re.compile(r"^[A-Za-z_@][A-Za-z0-9_.]*$")

# Hard cap on `limit` passed to filter_log_events, not caller-configurable.
FILTER_EVENTS_CAP = 1000

# Back-solving a suggested retry window targets this fraction of the byte
# ceiling, not 100% of it.
BYTES_SCANNED_SAFETY_MARGIN = 0.8
