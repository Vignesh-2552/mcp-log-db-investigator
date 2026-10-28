import re

# Shared between CloudWatch's and New Relic's describe_log_fields: flags column/attribute
# names that look like trace/span/request/correlation identifiers, so field-discovery hints
# stay consistent across sources instead of drifting via copy-pasted regexes.
CORRELATION_ID_RE = re.compile(
    r"(trace|span|request|correlation|order|user|session|transaction)[._-]?id", re.IGNORECASE
)
