from typing import Literal

from opsinsight.core.app import mcp
from opsinsight.core.container import get_container


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def ticket_search_issues(
    query: str | None = None,
    provider: Literal["linear", "jira"] | None = None,
    limit: int = 20,
    jql: str | None = None,
) -> dict:
    """Find Linear or Jira tickets when you don't have the id: pass a few words from
    the title or description in `query` (plain text, max 200 characters). For Jira
    only, pass a JQL filter in `jql` instead (e.g. `project = SUP AND status = "In
    Progress"`); results are ordered newest-updated first unless the JQL orders them.
    Returns key, title, status, assignee, url and updated time per ticket; call
    `ticket_get_issue` for the full description. `limit` is clamped server-side
    (`meta.limit_clamped`). Read-only."""
    return await get_container().ticket_service.search_tickets(query, provider, limit, jql)
