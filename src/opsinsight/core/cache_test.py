from opsinsight.core.cache import ttl_cache


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


def test_ttl_cache_caches_sync_functions_per_argument():
    calls: list[int] = []

    @ttl_cache(ttl_seconds=60)
    def square(x: int) -> int:
        calls.append(x)
        return x * x

    assert square(3) == 9
    assert square(3) == 9
    assert square(4) == 16
    assert calls == [3, 4]

    square.cache_clear()
    assert square(3) == 9
    assert calls == [3, 4, 3]


async def test_ttl_cache_clear_works_for_async_functions():
    calls = 0

    @ttl_cache(ttl_seconds=60)
    async def value():
        nonlocal calls
        calls += 1
        return calls

    assert await value() == 1
    value.cache_clear()
    assert await value() == 2
