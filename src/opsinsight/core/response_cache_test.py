import pytest

from opsinsight.core.response_cache import (
    KEY_PREFIX,
    MemoryBackend,
    ResponseCache,
    scope_fingerprint,
)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class _BrokenBackend:
    name = "broken"

    def __init__(self) -> None:
        self.calls = 0

    def get(self, key):
        self.calls += 1
        raise ConnectionError("redis down")

    def set(self, key, value, ttl_s):
        self.calls += 1
        raise ConnectionError("redis down")

    async def aget(self, key):
        return self.get(key)

    async def aset(self, key, value, ttl_s):
        self.set(key, value, ttl_s)

    def clear(self):
        pass


def _ok(n=1):
    return {"ok": True, "data": {"n": n}, "meta": {}}


def test_second_call_is_a_hit_and_skips_the_producer():
    cache = ResponseCache(MemoryBackend())
    calls = []

    def produce():
        calls.append(1)
        return _ok()

    first = cache.cached("ns", "scope", ["a"], 60, produce)
    second = cache.cached("ns", "scope", ["a"], 60, produce)

    assert len(calls) == 1
    assert first["meta"]["cache"] == "miss"
    assert second == {"ok": True, "data": {"n": 1}, "meta": {"cache": "hit"}}


async def test_async_hit():
    cache = ResponseCache(MemoryBackend())
    calls = []

    async def produce():
        calls.append(1)
        return _ok()

    await cache.acached("ns", "scope", ["a"], 60, produce)
    second = await cache.acached("ns", "scope", ["a"], 60, produce)

    assert len(calls) == 1
    assert second["meta"]["cache"] == "hit"


def test_errors_are_not_cached():
    cache = ResponseCache(MemoryBackend())
    calls = []

    def produce():
        calls.append(1)
        return {"ok": False, "error": {"rule": "x"}}

    cache.cached("ns", "scope", [], 60, produce)
    cache.cached("ns", "scope", [], 60, produce)

    assert len(calls) == 2


def test_scope_and_arguments_separate_entries():
    cache = ResponseCache(MemoryBackend())
    cache.cached("ns", "db-a", ["t"], 60, lambda: _ok(1))

    assert cache.cached("ns", "db-b", ["t"], 60, lambda: _ok(2))["data"]["n"] == 2
    assert cache.cached("ns", "db-a", ["u"], 60, lambda: _ok(3))["data"]["n"] == 3


def test_entries_expire_after_ttl(monkeypatch: pytest.MonkeyPatch):
    clock = _Clock()
    monkeypatch.setattr("opsinsight.core.response_cache.time.monotonic", clock)
    cache = ResponseCache(MemoryBackend())
    cache.cached("ns", "s", [], 10, lambda: _ok(1))

    clock.now = 11
    assert cache.cached("ns", "s", [], 10, lambda: _ok(2))["data"]["n"] == 2


def test_zero_ttl_and_no_backend_bypass_the_cache():
    calls = []

    def produce():
        calls.append(1)
        return _ok()

    for cache, ttl in ((ResponseCache(MemoryBackend()), 0), (ResponseCache(None), 60)):
        cache.cached("ns", "s", [], ttl, produce)
        result = cache.cached("ns", "s", [], ttl, produce)
        assert "cache" not in result["meta"]
    assert len(calls) == 4


def test_backend_failure_fails_open_and_backs_off():
    clock = _Clock()
    backend = _BrokenBackend()
    cache = ResponseCache(backend, retry_after_s=30, clock=clock)

    assert cache.cached("ns", "s", [], 60, _ok)["ok"] is True
    assert backend.calls == 1  # read failed; the write is skipped while backing off
    cache.cached("ns", "s", [], 60, _ok)
    assert backend.calls == 1  # bypassed: no timeout paid on every call

    clock.now = 31
    cache.cached("ns", "s", [], 60, _ok)
    assert backend.calls == 2


async def test_async_backend_failure_fails_open():
    cache = ResponseCache(_BrokenBackend())

    async def produce():
        return _ok()

    assert (await cache.acached("ns", "s", [], 60, produce))["ok"] is True


def test_keys_never_contain_raw_arguments():
    key = ResponseCache.key("cw.insights", "s", ["filter email = 'jane@example.com'"])

    assert key.startswith(f"{KEY_PREFIX}cw.insights:")
    assert "jane" not in key


def test_scope_fingerprint_hides_credentials():
    fingerprint = scope_fingerprint("db", "postgresql://u:s3cret@host/db")

    assert "s3cret" not in fingerprint
    assert len(fingerprint) == 16


def test_oversized_responses_are_not_stored():
    cache = ResponseCache(MemoryBackend(), max_value_bytes=50)
    calls = []

    def produce():
        calls.append(1)
        return {"ok": True, "data": {"blob": "x" * 100}, "meta": {}}

    cache.cached("ns", "s", [], 60, produce)
    cache.cached("ns", "s", [], 60, produce)

    assert len(calls) == 2


def test_should_store_predicate():
    cache = ResponseCache(MemoryBackend())
    calls = []

    def produce():
        calls.append(1)
        return {"ok": True, "data": {"status": "Stopped"}, "meta": {}}

    for _ in range(2):
        cache.cached("ns", "s", [], 60, produce, should_store=lambda r: r["data"]["status"] == "Complete")

    assert len(calls) == 2
