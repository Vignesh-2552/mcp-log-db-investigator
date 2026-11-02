import json
import logging

import pytest
from fastmcp import Client

from opsinsight import resources as _resources  # noqa: F401  (registers resources)
from opsinsight import tools as _tools  # noqa: F401  (registers tools)
from opsinsight.core.app import mcp
from opsinsight.core.audit import MAX_AUDIT_ARG_CHARS, audit_logger

# Every tool this server exposes. A new tool must be reviewed for authorization,
# redaction and audit before being added here (docs/security-audit.md).
EXPECTED_TOOLS = {
    "db_list_tables",
    "db_describe_table",
    "db_sample_rows",
    "db_explain_query",
    "db_run_query",
    "db_search_by_identifier",
    "db_resolve_store",
    "cw_list_log_groups",
    "cw_describe_log_fields",
    "cw_run_insights_query",
    "cw_get_trace_events",
    "cw_filter_events",
    "cw_get_metric_stats",
    "nr_list_event_types",
    "nr_describe_log_fields",
    "nr_run_nrql_query",
    "trace_request",
}


class _Collector(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.events: list[dict] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.events.append(json.loads(record.getMessage()))


@pytest.fixture
def audit_events():
    handler = _Collector()
    previous_level = audit_logger.level
    audit_logger.addHandler(handler)
    audit_logger.setLevel(logging.INFO)
    yield handler.events
    audit_logger.removeHandler(handler)
    audit_logger.setLevel(previous_level)


async def test_registered_tools_match_the_reviewed_inventory():
    async with Client(mcp) as client:
        names = {tool.name for tool in await client.list_tools()}
    assert names == EXPECTED_TOOLS


async def test_rejected_tool_call_is_audited_with_redacted_arguments(audit_events):
    sql = "DELETE FROM public.users WHERE email = 'victim@example.com'"
    async with Client(mcp) as client:
        result = await client.call_tool("db_run_query", {"sql": sql})

    assert result.structured_content["ok"] is False
    [event] = [e for e in audit_events if e["event"] == "tool_call"]
    assert event["tool"] == "db_run_query"
    assert event["outcome"] == "rejected"
    assert event["rule"] in ("blocked_operation", "root_not_select")
    assert event["actor"] == "unauthenticated"
    assert "victim@example.com" not in json.dumps(event)
    assert "[REDACTED_EMAIL]" in event["arguments"]["sql"]
    assert event["duration_ms"] >= 0


async def test_audit_arguments_are_truncated_and_cannot_inject_lines(audit_events):
    sql = "SELECT 1 -- " + "x" * (MAX_AUDIT_ARG_CHARS + 500) + '\n{"event": "forged"}'
    async with Client(mcp) as client:
        await client.call_tool("db_explain_query", {"sql": "INSERT INTO t VALUES (1); " + sql})

    [event] = [e for e in audit_events if e["event"] == "tool_call"]
    assert "truncated" in event["arguments"]["sql"]
    assert len(event["arguments"]["sql"]) < MAX_AUDIT_ARG_CHARS + 100
    assert not any(e.get("event") == "forged" for e in audit_events)
