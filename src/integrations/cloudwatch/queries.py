ERROR_BOOST_FILTER_PATTERN = "?ERROR ?error ?Error ?FATAL ?fatal ?CRITICAL ?critical"


def build_trace_query(field: str, escaped_value: str) -> str:
    """`field`/`escaped_value` must already be guardrail-validated/escaped by
    the caller (`validate_trace_field`/`build_trace_filter_value`)."""
    return f'fields @timestamp, @message | filter {field} = "{escaped_value}" | sort @timestamp asc'
