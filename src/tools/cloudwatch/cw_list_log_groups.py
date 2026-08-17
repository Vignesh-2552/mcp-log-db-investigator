from botocore.exceptions import BotoCoreError, ClientError

from core.app import mcp
from core.config import get_settings
from tools.cloudwatch import utils


@mcp.tool()
def cw_list_log_groups(prefix: str | None = None) -> dict:
    """List log groups (name, retention, stored bytes), filtered to the allowlist."""
    settings = get_settings()
    allowlist = settings.cw_log_group_allowlist_set
    try:
        client = utils.get_logs_client(settings)
        kwargs = {"logGroupNamePrefix": prefix} if prefix else {}
        groups = []
        paginator = client.get_paginator("describe_log_groups")
        for page in paginator.paginate(**kwargs):
            for g in page.get("logGroups", []):
                name = g.get("logGroupName", "")
                if allowlist and name not in allowlist:
                    continue
                groups.append(
                    {
                        "log_group": name,
                        "retention_days": g.get("retentionInDays"),
                        "stored_bytes": g.get("storedBytes"),
                    }
                )
    except (BotoCoreError, ClientError) as e:
        return utils.aws_error_response(e)
    return {"ok": True, "data": {"log_groups": groups}, "meta": {"row_count": len(groups)}}
