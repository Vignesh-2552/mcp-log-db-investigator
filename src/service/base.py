from abc import ABC

from core.config import Settings


class BaseService(ABC):
    """Common base for source-specific services (`DatabaseService`,
    `CloudWatchService`, `NewRelicService`).

    Owns the `Settings` each service needs (encapsulation: subclasses read
    `self.settings` instead of each method calling `get_settings()` itself)
    and centralizes the success-response envelope so every service builds
    `{"ok": True, "data": ..., "meta": ...}` the same way. Error handling is
    deliberately left to each subclass rather than forced through an
    abstract method here — DB, CloudWatch, and New Relic each raise a
    different exception family (`SQLAlchemyError`,
    `BotoCoreError`/`ClientError`, `httpx.HTTPError`), and forcing one
    shared error-handling contract onto all three would violate interface
    segregation for no benefit.
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    @property
    def settings(self) -> Settings:
        return self._settings

    @staticmethod
    def ok(data: dict, meta: dict | None = None) -> dict:
        return {"ok": True, "data": data, "meta": meta if meta is not None else {}}
