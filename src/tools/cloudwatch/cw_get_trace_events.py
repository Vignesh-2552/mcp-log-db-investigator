from core.app import mcp
from core.container import get_container


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
    return get_container().cloudwatch_service.get_trace_events(
        log_group, field, value, start, end, limit, include_ptr
    )
