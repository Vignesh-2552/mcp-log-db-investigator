"""Every MCP tool and resource, called through a real FastMCP client.

Tool files are one-line wrappers, so the risk is wiring: the wrong service,
method or argument order. Each call here goes through a container whose
services are recorders, and checks which method got which arguments."""

import json

import pytest
from fastmcp import Client

from opsinsight import resources as _resources  # noqa: F401  (registers resources)
from opsinsight import tools as _tools  # noqa: F401  (registers tools)
from opsinsight.core import container as container_module
from opsinsight.core.app import mcp
from opsinsight.core.config import get_settings
from opsinsight.core.container import Container


class _Recorder:
    """Stands in for a service: any method call is recorded and returns an ok envelope."""

    def __init__(self, is_async: bool) -> None:
        self.is_async = is_async
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def method(*args, **kwargs):
            self.calls.append((name, args, kwargs))
            response = {"ok": True, "data": {"method": name}, "meta": {"row_count": 0}}
            if self.is_async:

                async def _done():
                    return response

                return _done()
            return response

        return method


@pytest.fixture
def services(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    fakes = {
        "database_service": _Recorder(is_async=True),
        "cloudwatch_service": _Recorder(is_async=False),
        "newrelic_service": _Recorder(is_async=True),
        "trace_service": _Recorder(is_async=True),
    }
    monkeypatch.setattr(container_module, "_container", Container(**fakes))
    yield fakes
    get_settings.cache_clear()


# (tool, arguments, service, method, expected positional args, expected kwargs)
CASES = [
    ("db_list_tables", {"schema": "public"}, "database_service", "list_tables", ("public",), {}),
    ("db_describe_table", {"table": "public.orders"}, "database_service", "describe_table",
     ("public.orders",), {}),
    ("db_sample_rows", {"table": "public.orders", "limit": 3}, "database_service", "sample_rows",
     ("public.orders", 3), {}),
    ("db_explain_query", {"sql": "SELECT 1"}, "database_service", "explain_query", ("SELECT 1",), {}),
    ("db_run_query", {"sql": "SELECT 1", "limit": 10}, "database_service", "run_query", ("SELECT 1", 10), {}),
    ("db_search_by_identifier", {"identifier": "42", "id_type": "order_id"}, "database_service",
     "search_by_identifier", ("42", "order_id"), {}),
    ("db_resolve_store", {"name_or_domain": "shop.example"}, "database_service", "resolve_store",
     ("shop.example",), {}),
    ("cw_list_log_groups", {"prefix": "/aws"}, "cloudwatch_service", "list_log_groups", ("/aws",), {}),
    ("cw_describe_log_fields", {"log_group": "/g"}, "cloudwatch_service", "describe_log_fields", ("/g",), {}),
    ("cw_run_insights_query",
     {"log_groups": ["/g"], "query": "fields @message", "start": "s", "end": "e", "limit": 5},
     "cloudwatch_service", "run_insights_query", (["/g"], "fields @message", "s", "e", 5, False), {}),
    ("cw_get_trace_events",
     {"log_group": "/g", "field": "requestId", "value": "r1", "start": "s", "end": "e"},
     "cloudwatch_service", "get_trace_events", ("/g", "requestId", "r1", "s", "e", 200, False), {}),
    ("cw_filter_events", {"log_group": "/g", "pattern": "ERROR", "minutes": 5}, "cloudwatch_service",
     "filter_events", ("/g", "ERROR", 5), {}),
    ("cw_get_metric_stats",
     {"namespace": "AWS/ECS", "metric": "CPU", "dimensions": {"Svc": "a"}, "period": 60, "start": "s",
      "end": "e"},
     "cloudwatch_service", "get_metric_stats", ("AWS/ECS", "CPU", {"Svc": "a"}, 60, "s", "e"), {}),
    ("nr_list_event_types", {"hours": 6}, "newrelic_service", "list_event_types", (6,), {}),
    ("nr_describe_log_fields", {"event_type": "Log", "hours": 2}, "newrelic_service",
     "describe_log_fields", ("Log", 2), {}),
    ("nr_run_nrql_query", {"query": "SELECT * FROM Log", "limit": 7}, "newrelic_service",
     "run_nrql_query", ("SELECT * FROM Log", 7), {}),
    ("trace_request", {"value": "r1", "sources": ["db"], "db_id_type": "order_id"}, "trace_service",
     "trace_request", ("r1",),
     {"start": None, "end": None, "sources": ["db"], "cw_log_groups": None, "cw_field": None,
      "nr_event_type": None, "nr_field": None, "db_id_type": "order_id", "limit": 200}),
]


def test_every_registered_tool_has_a_wiring_case():
    from tests.unit.test_audit import EXPECTED_TOOLS

    assert {case[0] for case in CASES} == EXPECTED_TOOLS


@pytest.mark.parametrize(("tool", "arguments", "service", "method", "args", "kwargs"), CASES,
                         ids=[c[0] for c in CASES])
async def test_tool_calls_the_matching_service_method(services, tool, arguments, service, method, args, kwargs):
    async with Client(mcp) as client:
        result = await client.call_tool(tool, arguments)

    assert result.structured_content == {"ok": True, "data": {"method": method}, "meta": {"row_count": 0}}
    assert services[service].calls == [(method, args, kwargs)]


async def test_query_cookbook_resource_renders_every_entry():
    import yaml

    from opsinsight.resources.cookbook import _COOKBOOK_PATH

    entries = yaml.safe_load(_COOKBOOK_PATH.read_text(encoding="utf-8"))
    async with Client(mcp) as client:
        [content] = await client.read_resource("docs://query-cookbook")

    text = content.text
    assert text.startswith("# Query Cookbook")
    for entry in entries:
        assert f"## {entry['title']}" in text
    assert "```sql" in text
    assert text.count("```") == 2 * len(entries)


def test_cookbook_entries_have_required_fields():
    import yaml

    from opsinsight.resources.cookbook import _COOKBOOK_PATH

    entries = yaml.safe_load(_COOKBOOK_PATH.read_text(encoding="utf-8"))
    ids = [e["id"] for e in entries]
    assert len(ids) == len(set(ids)), "cookbook ids must be unique"
    for entry in entries:
        assert {"id", "title", "kind", "query"} <= entry.keys(), entry.get("id")
        assert entry["query"].strip()


async def test_log_groups_resource_reuses_the_list_tool(services):
    async with Client(mcp) as client:
        [content] = await client.read_resource("logs://groups")

    assert json.loads(content.text)["data"]["method"] == "list_log_groups"
    assert services["cloudwatch_service"].calls == [("list_log_groups", (None,), {})]
