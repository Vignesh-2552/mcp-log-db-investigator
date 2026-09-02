from core.cache import ttl_cache


async def test_ttl_cache_uses_updated_ttl_provider_value():
    ttl = 60
    calls = 0

    @ttl_cache(ttl_seconds=lambda: ttl)
    async def cached_value():
        nonlocal calls
        calls += 1
        return calls

    assert await cached_value() == 1
    assert await cached_value() == 1

    ttl = 0

    assert await cached_value() == 2
    assert await cached_value() == 3
