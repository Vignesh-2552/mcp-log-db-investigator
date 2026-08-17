from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from integrations.cloudwatch.guardrail import (
    build_trace_filter_value,
    validate_trace_field,
)
from tools.cloudwatch import utils


@mcp.tool()
def cw_get_trace_events(
    log_group: str,
    field: str,
    value: str,
    start: str,
    end: str,
    limit: int = 200,
    include_ptr: bool = False,
) -> dict:
    """Pull every log line in `log_group` where `field` equals `value`, sorted
    chronologically — replaces manually re-running multiple Insights queries to
    reconstruct a trace by hand. Discover `field` via
    cw_describe_log_fields's per-shape `correlation_id_candidates` hint.
    CloudWatch-only: does not fan out to the database or New Relic."""
    settings = get_settings()
    try:
        validate_trace_field(field)
        escaped_value = build_trace_filter_value(value)
    except ToolError as e:
        return e.to_response()
    query = f'fields @timestamp, @message | filter {field} = "{escaped_value}" | sort @timestamp asc'
    response = utils.run_insights_query(settings, [log_group], query, start, end, limit, include_ptr)
    if response.get("ok"):
        response["data"].update(
            {"log_group": log_group, "field": field, "value": value, "executed_query": query}
        )
    return response
