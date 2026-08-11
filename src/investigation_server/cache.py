import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from cachetools import TTLCache
from cachetools.keys import hashkey

F = TypeVar("F", bound=Callable[..., Any])


def ttl_cache(maxsize: int = 128, ttl_seconds: int = 600) -> Callable[[F], F]:
    """Decorator caching a function's return value for `ttl_seconds`.

    Unlike `functools.lru_cache`, the TTL is enforced per-entry, and the
    cache can be cleared per-decorated-function via `.cache_clear()` —
    used for the 10-minute schema/log-group caches (doc §4.1/§4.2).

    Works on both sync and async functions (async ones are awaited before
    caching the resolved value, not the coroutine object).
    """

    def decorator(func: F) -> F:
        cache: TTLCache = TTLCache(maxsize=maxsize, ttl=ttl_seconds)

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                key = hashkey(*args, **kwargs)
                try:
                    return cache[key]
                except KeyError:
                    result = await func(*args, **kwargs)
                    cache[key] = result
                    return result

            async_wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            key = hashkey(*args, **kwargs)
            try:
                return cache[key]
            except KeyError:
                result = func(*args, **kwargs)
                cache[key] = result
                return result

        wrapper.cache_clear = cache.clear  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
