"""Shared cache for tool responses (CACHE_BACKEND=memory|redis|none).

Services cache whole *successful, already-redacted* responses, never raw
rows, and only data that is stable for the TTL: catalog/inventory metadata
and CloudWatch Insights results for windows that have already closed.

Design rules:
- **Fail open.** A cache error is logged and treated as a miss; it never
  fails a tool call. After an error the backend is skipped for
  `CACHE_RETRY_AFTER_S`, so an unreachable Redis costs one timeout per
  interval instead of one per call.
- **No raw arguments in keys.** Keys are `<prefix><namespace>:<sha256>`
  over the call's arguments and the *scope* (which DB/account/region, and
  whether redaction was on), so SQL text, identifiers or a DSN never appear
  in Redis key names, and redacted and unredacted responses never mix.
- **Both sync and async.** CloudWatch services are sync (FastMCP runs them
  in a worker thread); DB and New Relic are async.
"""

import hashlib
import json
import threading
import time
from collections.abc import Awaitable, Callable
from typing import Any, Protocol

from cachetools import TLRUCache

from opsinsight.core.logging_config import get_logger

logger = get_logger("response_cache")

KEY_PREFIX = "opsinsight:v1:"


class CacheBackend(Protocol):
    """Stores opaque bytes with a per-entry TTL."""

    name: str

    def get(self, key: str) -> bytes | None: ...
    def set(self, key: str, value: bytes, ttl_s: int) -> None: ...
    async def aget(self, key: str) -> bytes | None: ...
    async def aset(self, key: str, value: bytes, ttl_s: int) -> None: ...
    def clear(self) -> None: ...


class MemoryBackend:
    """Per-process cache with per-entry TTL and LRU eviction."""

    name = "memory"

    def __init__(self, maxsize: int = 1024) -> None:
        self._cache: TLRUCache = TLRUCache(
            maxsize=maxsize, ttu=lambda _key, value, now: now + value[0], timer=time.monotonic
        )
        self._lock = threading.Lock()

    def get(self, key: str) -> bytes | None:
        with self._lock:
            entry = self._cache.get(key)
        return entry[1] if entry is not None else None

    def set(self, key: str, value: bytes, ttl_s: int) -> None:
        with self._lock:
            self._cache[key] = (ttl_s, value)

    async def aget(self, key: str) -> bytes | None:
        return self.get(key)

    async def aset(self, key: str, value: bytes, ttl_s: int) -> None:
        self.set(key, value, ttl_s)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()


def scope_fingerprint(*parts: Any) -> str:
    """Short hash identifying *what* a response came from (DB DSN, AWS
    account/region, NR account, redaction on/off). Hashed so credentials and
    hostnames never end up in key names."""
    return hashlib.sha256(json.dumps(parts, default=str).encode()).hexdigest()[:16]


def _store_ok(response: dict) -> bool:
    return response.get("ok") is True


class ResponseCache:
    def __init__(
        self,
        backend: CacheBackend | None,
        retry_after_s: float = 30.0,
        max_value_bytes: int = 2_000_000,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._backend = backend
        self._retry_after_s = retry_after_s
        self._max_value_bytes = max_value_bytes
        self._clock = clock
        self._skip_until = 0.0

    @property
    def backend_name(self) -> str:
        return self._backend.name if self._backend else "none"

    @property
    def enabled(self) -> bool:
        return self._backend is not None

    @staticmethod
    def key(namespace: str, scope: str, parts: Any) -> str:
        digest = hashlib.sha256(json.dumps([scope, parts], default=str, sort_keys=True).encode()).hexdigest()
        return f"{KEY_PREFIX}{namespace}:{digest}"

    # --- fail-open plumbing -------------------------------------------------

    def _available(self) -> bool:
        return self._backend is not None and self._clock() >= self._skip_until

    def _failed(self, op: str, e: Exception) -> None:
        self._skip_until = self._clock() + self._retry_after_s
        logger.warning(
            "Cache %s failed on %s backend (%s: %s); bypassing cache for %.0fs",
            op, self.backend_name, type(e).__name__, e, self._retry_after_s,
        )

    def _encode(self, response: dict) -> bytes | None:
        try:
            data = json.dumps(response, default=str).encode("utf-8")
        except (TypeError, ValueError) as e:
            logger.debug("Response not cacheable: %s", e)
            return None
        return data if len(data) <= self._max_value_bytes else None

    @staticmethod
    def _decode(raw: bytes | None) -> dict | None:
        if raw is None:
            return None
        try:
            value = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return None
        return value if isinstance(value, dict) else None

    @staticmethod
    def _mark(response: dict, status: str) -> dict:
        response.setdefault("meta", {})["cache"] = status
        return response

    # --- public API ---------------------------------------------------------

    def cached(
        self,
        namespace: str,
        scope: str,
        parts: Any,
        ttl_s: int,
        producer: Callable[[], dict],
        should_store: Callable[[dict], bool] = _store_ok,
    ) -> dict:
        """Returns the cached response (meta.cache="hit") or calls `producer`
        and stores its result when `should_store` says so (meta.cache="miss").
        ttl_s <= 0 or no backend: just calls `producer`."""
        if ttl_s <= 0 or not self.enabled:
            return producer()
        key = self.key(namespace, scope, parts)
        if self._available():
            try:
                hit = self._decode(self._backend.get(key))
            except Exception as e:  # noqa: BLE001 - fail open on any backend error
                self._failed("read", e)
                hit = None
            if hit is not None:
                return self._mark(hit, "hit")
        response = producer()
        if should_store(response) and self._available():
            encoded = self._encode(response)
            if encoded is not None:
                try:
                    self._backend.set(key, encoded, ttl_s)
                except Exception as e:  # noqa: BLE001
                    self._failed("write", e)
        return self._mark(response, "miss")

    async def acached(
        self,
        namespace: str,
        scope: str,
        parts: Any,
        ttl_s: int,
        producer: Callable[[], Awaitable[dict]],
        should_store: Callable[[dict], bool] = _store_ok,
    ) -> dict:
        """Async twin of `cached`."""
        if ttl_s <= 0 or not self.enabled:
            return await producer()
        key = self.key(namespace, scope, parts)
        if self._available():
            try:
                hit = self._decode(await self._backend.aget(key))
            except Exception as e:  # noqa: BLE001
                self._failed("read", e)
                hit = None
            if hit is not None:
                return self._mark(hit, "hit")
        response = await producer()
        if should_store(response) and self._available():
            encoded = self._encode(response)
            if encoded is not None:
                try:
                    await self._backend.aset(key, encoded, ttl_s)
                except Exception as e:  # noqa: BLE001
                    self._failed("write", e)
        return self._mark(response, "miss")

    def clear(self) -> None:
        if self._backend is not None:
            self._backend.clear()


NULL_CACHE = ResponseCache(None)
