import inspect
from collections.abc import Callable
from functools import wraps
from typing import Any, TypeVar

from cachetools import TTLCache
from cachetools.keys import hashkey

from core.logging_config import get_logger

logger = get_logger("cache")

F = TypeVar("F", bound=Callable[..., Any])


def ttl_cache(
    maxsize: int = 128, ttl_seconds: int | Callable[[], int] = 600
) -> Callable[[F], F]:
    """Decorator caching a function's return value for `ttl_seconds`.

    `ttl_seconds` may be a provider for settings-backed TTLs. If its value
    changes, existing entries are discarded and a cache with the new TTL is
    used.

    Unlike `functools.lru_cache`, the TTL is enforced per-entry, and the
    cache can be cleared per-decorated-function via `.cache_clear()` —
    used for the 10-minute schema/log-group caches (doc §4.1/§4.2).

    Works on both sync and async functions (async ones are awaited before
    caching the resolved value, not the coroutine object).
    """

    def decorator(func: F) -> F:
        configured_ttl = ttl_seconds() if callable(ttl_seconds) else ttl_seconds
        cache: TTLCache = TTLCache(maxsize=maxsize, ttl=configured_ttl)

        def current_cache() -> TTLCache:
            nonlocal cache, configured_ttl
            current_ttl = ttl_seconds() if callable(ttl_seconds) else ttl_seconds
            if current_ttl != configured_ttl:
                configured_ttl = current_ttl
                cache = TTLCache(maxsize=maxsize, ttl=current_ttl)
            return cache

        def cache_clear() -> None:
            cache.clear()

        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                active_cache = current_cache()
                key = hashkey(*args, **kwargs)
                try:
                    res = active_cache[key]
                    logger.debug("Cache hit for %s", func.__name__)
                    return res
                except KeyError:
                    logger.debug("Cache miss for %s", func.__name__)
                    result = await func(*args, **kwargs)
                    active_cache[key] = result
                    return result

            async_wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            active_cache = current_cache()
            key = hashkey(*args, **kwargs)
            try:
                res = active_cache[key]
                logger.debug("Cache hit for %s", func.__name__)
                return res
            except KeyError:
                logger.debug("Cache miss for %s", func.__name__)
                result = func(*args, **kwargs)
                active_cache[key] = result
                return result

        wrapper.cache_clear = cache_clear  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorator
