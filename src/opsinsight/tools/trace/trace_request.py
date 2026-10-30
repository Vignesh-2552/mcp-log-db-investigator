from typing import Literal

from opsinsight.core.app import mcp
from opsinsight.core.container import get_container


@mcp.tool()
async def trace_request(
    value: str,
    start: str | None = None,
    end: str | None = None,
    sources: list[Literal["cloudwatch", "newrelic", "db"]] | None = None,
    cw_log_groups: list[str] | None = None,
    cw_field: str | None = None,
    nr_event_type: str | None = None,
    nr_field: str | None = None,
    db_id_type: str | None = None,
    limit: int = 200,
) -> dict:
    """Follow one identifier (request id, order id, email, ...) across CloudWatch,
    New Relic and the database at once and get back ONE merged timeline, sorted
    oldest first, each event tagged with its `source` and `origin`.

    Use this first when a ticket gives you an identifier; fall back to the
    per-source tools to dig deeper. `start`/`end` are ISO 8601 (default: the
    last 24h). Field names default to server config (TRACE_CW_FIELD,
    TRACE_NR_FIELD, TRACE_DB_ID_TYPE); if a source returns nothing, check the
    right field name with cw_describe_log_fields / nr_describe_log_fields /
    db_describe_table and pass it here. The DB is only searched when
    `db_id_type` (a column name such as "order_id") is given or configured, and
    DB rows are NOT filtered by the time window (see each event's `in_window`).

    One source failing does not fail the call: read `data.sources` for each
    source's status, executed query and error. Cite event timestamps and row
    counts from the result rather than inferring them."""
    return await get_container().trace_service.trace_request(
        value,
        start=start,
        end=end,
        sources=list(sources) if sources else None,
        cw_log_groups=cw_log_groups,
        cw_field=cw_field,
        nr_event_type=nr_event_type,
        nr_field=nr_field,
        db_id_type=db_id_type,
        limit=limit,
    )
