from fastmcp.server.middleware.rate_limiting import RateLimitingMiddleware

from opsinsight.core.config import Settings


def build_rate_limiter(settings: Settings) -> RateLimitingMiddleware | None:
    """Token-bucket limit on every MCP request, or None when RATE_LIMIT_RPS=0.

    Global rather than per-client: HTTP clients all share one static token
    (one identity) and stdio has a single client, so a per-client bucket
    would behave the same while suggesting isolation that isn't there.
    Over-limit requests fail with a JSON-RPC error; the audit middleware,
    registered first and therefore outermost, still records them."""
    if settings.rate_limit_rps <= 0:
        return None
    return RateLimitingMiddleware(
        max_requests_per_second=settings.rate_limit_rps,
        burst_capacity=max(1, settings.rate_limit_burst),
        global_limit=True,
    )
