import pytest

from opsinsight.core.config import Settings
from opsinsight.core.response_cache import KEY_PREFIX, ResponseCache
from opsinsight.integrations.redis.client import RedisBackend, RedisBackendError


class _FakeRedis:
    """The subset of redis.Redis the backend uses."""

    def __init__(self) -> None:
        self.store: dict[str, bytes] = {}
        self.ttls: dict[str, int] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value, ex=None):
        self.store[key] = value
        self.ttls[key] = ex

    def scan_iter(self, match=None, count=None):
        prefix = match.rstrip("*")
        return [k for k in self.store if k.startswith(prefix)]

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)

    def ping(self):
        return True


class _FakeAsyncRedis:
    def __init__(self, sync: _FakeRedis) -> None:
        self._sync = sync

    async def get(self, key):
        return self._sync.get(key)

    async def set(self, key, value, ex=None):
        self._sync.set(key, value, ex=ex)


def _backend() -> tuple[RedisBackend, _FakeRedis]:
    fake = _FakeRedis()
    return RedisBackend(fake, _FakeAsyncRedis(fake)), fake


def test_set_uses_ttl_and_get_round_trips():
    backend, fake = _backend()
    cache = ResponseCache(backend)

    cache.cached("db.list_tables", "s", [None], 600, lambda: {"ok": True, "data": {"tables": []}, "meta": {}})
    hit = cache.cached("db.list_tables", "s", [None], 600, lambda: {"ok": True, "data": {}, "meta": {}})

    assert hit["meta"]["cache"] == "hit"
    assert hit["data"] == {"tables": []}
    assert list(fake.ttls.values()) == [600]


async def test_async_round_trip():
    backend, _ = _backend()
    cache = ResponseCache(backend)

    async def produce():
        return {"ok": True, "data": {"x": 1}, "meta": {}}

    await cache.acached("nr.x", "s", [], 60, produce)
    assert (await cache.acached("nr.x", "s", [], 60, produce))["meta"]["cache"] == "hit"


def test_clear_deletes_only_this_servers_keys():
    backend, fake = _backend()
    fake.store["other-app:key"] = b"keep"
    fake.store[f"{KEY_PREFIX}db.x:abc"] = b"drop"

    backend.clear()

    assert fake.store == {"other-app:key": b"keep"}


def test_redis_backend_requires_url():
    with pytest.raises(ValueError, match="REDIS_URL"):
        Settings(cache_backend="redis")
    with pytest.raises(RedisBackendError, match="REDIS_URL"):
        RedisBackend.from_settings(Settings())


def test_from_settings_builds_clients_with_timeouts():
    backend = RedisBackend.from_settings(
        Settings(redis_url="redis://localhost:6399/0", redis_socket_timeout_s=0.25)
    )

    pool_kwargs = backend._sync.connection_pool.connection_kwargs
    assert pool_kwargs["socket_timeout"] == 0.25
    assert pool_kwargs["port"] == 6399
