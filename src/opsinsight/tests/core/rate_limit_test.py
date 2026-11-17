"""core.rate_limit: the global request rate limiter."""

import json

from fastmcp import Client, FastMCP
from mcp.shared.exceptions import MCPError

from opsinsight.core.config import Settings
from opsinsight.core.rate_limit import build_rate_limiter


def _row_bytes(row: dict) -> int:
    return len(json.dumps(row, default=str).encode("utf-8")) + 1


def test_rate_limiter_disabled_at_zero():
    assert build_rate_limiter(Settings(rate_limit_rps=0)) is None


def test_rate_limiter_uses_configured_rate_and_burst():
    limiter = build_rate_limiter(Settings(rate_limit_rps=2.5, rate_limit_burst=7))
    assert limiter.max_requests_per_second == 2.5
    assert limiter.burst_capacity == 7
    assert limiter.global_limit is True


async def test_rate_limiter_rejects_requests_past_the_burst():
    server = FastMCP("rate-limit-test")

    @server.tool()
    def ping() -> str:
        return "pong"

    # Near-zero refill: only the burst is available during the test. The
    # MCP initialize handshake itself consumes requests from the bucket.
    server.add_middleware(build_rate_limiter(Settings(rate_limit_rps=0.001, rate_limit_burst=4)))

    outcomes = []
    async with Client(server) as client:
        for _ in range(6):
            try:
                await client.call_tool("ping", {})
                outcomes.append("ok")
            except Exception as e:  # noqa: BLE001 - asserting on the error type below
                outcomes.append(e)

    assert outcomes[0] == "ok"
    rejected = [o for o in outcomes if o != "ok"]
    assert rejected, outcomes
    # A protocol-level error, not a tool result, so the call never ran.
    assert all(isinstance(e, MCPError) and "rate limit exceeded" in str(e).lower() for e in rejected)
