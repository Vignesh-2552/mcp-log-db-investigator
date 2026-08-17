from core.app import mcp
from core.container import get_container


@mcp.tool()
def cw_run_insights_query(
    log_groups: list[str],
    query: str,
    start: str,
    end: str,
    limit: int = 100,
    include_ptr: bool = False,
) -> dict:
    """Run a CloudWatch Logs Insights query and wait for results (StartQuery -> poll ->
    GetQueryResults). Logs bytes scanned on every call so cost stays visible.

    `limit` is the authoritative row cap sent to AWS's StartQuery API — set
    it directly (e.g. limit=10 for "last 10 logs") rather than relying on a
    `| limit N` clause inside `query`, which does not reliably cap the
    result count on its own.

    AWS's opaque `@ptr` row pointer (~300 chars, no consumer for it in this
    server today) is stripped from every row by default; pass
    include_ptr=True to keep it."""
    return get_container().cloudwatch_service.run_insights_query(
        log_groups, query, start, end, limit, include_ptr
    )
