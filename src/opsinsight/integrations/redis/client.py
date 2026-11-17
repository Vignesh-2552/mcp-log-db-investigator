"""Redis backend for `core.response_cache.ResponseCache` (CACHE_BACKEND=redis).

Holds one sync client (CloudWatch services run in worker threads) and one
asyncio client (DB / New Relic services). Short socket timeouts keep a slow
or unreachable Redis from stalling a tool call; `ResponseCache` treats any
error as a miss. `redis` is an optional dependency: `opsinsight-mcp[redis]`.
"""

from typing import Any

from opsinsight.core.config import Settings
from opsinsight.core.logging_config import get_logger
from opsinsight.core.response_cache import KEY_PREFIX

logger = get_logger("redis.client")


class RedisBackendError(RuntimeError):
    """Raised at startup when CACHE_BACKEND=redis can't be configured."""


class RedisBackend:
    name = "redis"

    def __init__(self, sync_client: Any, async_client: Any) -> None:
        self._sync = sync_client
        self._async = async_client

    @classmethod
    def from_settings(cls, settings: Settings) -> "RedisBackend":
        if settings.redis_url is None:
            raise RedisBackendError("CACHE_BACKEND=redis requires REDIS_URL (redis:// or rediss:// for TLS).")
        try:
            import redis
            import redis.asyncio
        except ImportError as e:
            raise RedisBackendError(
                "CACHE_BACKEND=redis needs the redis package: install `opsinsight-mcp[redis]`."
            ) from e
        url = settings.redis_url.get_secret_value()
        options = {
            "socket_timeout": settings.redis_socket_timeout_s,
            "socket_connect_timeout": settings.redis_socket_timeout_s,
        }
        logger.info("Response cache: Redis (timeout %.2fs)", settings.redis_socket_timeout_s)
        return cls(redis.Redis.from_url(url, **options), redis.asyncio.Redis.from_url(url, **options))

    def get(self, key: str) -> bytes | None:
        return self._sync.get(key)

    def set(self, key: str, value: bytes, ttl_s: int) -> None:
        self._sync.set(key, value, ex=ttl_s)

    async def aget(self, key: str) -> bytes | None:
        return await self._async.get(key)

    async def aset(self, key: str, value: bytes, ttl_s: int) -> None:
        await self._async.set(key, value, ex=ttl_s)

    def clear(self) -> None:
        """Deletes only this server's keys (KEY_PREFIX), never the whole DB."""
        keys = list(self._sync.scan_iter(match=f"{KEY_PREFIX}*", count=500))
        for start in range(0, len(keys), 500):
            self._sync.delete(*keys[start : start + 500])

    def ping(self) -> bool:
        return bool(self._sync.ping())
