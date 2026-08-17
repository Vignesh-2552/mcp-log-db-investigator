from datetime import UTC, datetime, timedelta

from botocore.exceptions import BotoCoreError, ClientError

from core.app import mcp
from core.config import get_settings
from core.errors import ToolError
from core.redaction import redact_log_event
from tools.cloudwatch import utils


@mcp.tool()
def cw_filter_events(log_group: str, pattern: str, minutes: int = 30) -> dict:
    """Filter raw log events by pattern over the last `minutes`. Cheaper than Insights
    for simple greps."""
    settings = get_settings()
    try:
        utils.validate_log_groups([log_group], settings.cw_log_group_allowlist_set)
        client = utils.get_logs_client(settings)
        end_dt = datetime.now(UTC)
        start_dt, end_dt = utils.clamp_window(
            (end_dt - timedelta(minutes=minutes)).isoformat(),
            end_dt.isoformat(),
            settings.cw_default_window_hours,
            settings.cw_max_window_hours,
        )
        response = client.filter_log_events(
            logGroupName=log_group,
            filterPattern=pattern,
            startTime=utils.epoch_millis(start_dt),
            endTime=utils.epoch_millis(end_dt),
            limit=utils.FILTER_EVENTS_CAP,
        )
    except ToolError as e:
        return e.to_response()
    except (BotoCoreError, ClientError) as e:
        return utils.aws_error_response(e)

    events = [
        {
            "timestamp": e.get("timestamp"),
            "message": redact_log_event(e.get("message", ""), settings),
            "log_stream": e.get("logStreamName"),
        }
        for e in response.get("events", [])
    ]
    return {
        "ok": True,
        "data": {"log_group": log_group, "events": events, "event_count": len(events)},
        "meta": {"row_count": len(events)},
    }
