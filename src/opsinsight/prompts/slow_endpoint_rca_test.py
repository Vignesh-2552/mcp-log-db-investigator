from fastmcp import Client

from opsinsight import prompts as _prompts  # noqa: F401  (registers prompts)
from opsinsight.core.app import mcp
from opsinsight.prompts.slow_endpoint_rca import slow_endpoint_rca


def test_defaults():
    text = slow_endpoint_rca("/api/checkout")
    assert "/api/checkout" in text
    assert "Time window: `4h`" in text
    assert "Latency threshold: 1000 ms" in text
    assert "duration > 1`" in text


def test_threshold_is_converted_to_seconds_for_nrql():
    text = slow_endpoint_rca("WebTransaction/Expressjs/POST//orders", threshold_ms=250)
    assert "duration > 0.25`" in text
    assert "share over 250 ms" in text


def test_metric_dimensions_are_asked_for_not_guessed():
    text = slow_endpoint_rca("/api/checkout")
    assert "`DBInstanceIdentifier`" in text
    assert "ask the user for them rather than guessing" in text
    assert "`db_explain_query`" in text


async def test_threshold_arrives_as_a_string_over_mcp():
    # MCP sends prompt arguments as strings; FastMCP must still coerce to int.
    async with Client(mcp) as client:
        result = await client.get_prompt(
            "slow_endpoint_rca", {"endpoint_or_transaction": "/api/checkout", "threshold_ms": "500"}
        )
    assert "duration > 0.5`" in result.messages[0].content.text
