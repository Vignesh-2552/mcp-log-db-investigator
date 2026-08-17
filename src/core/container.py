from core.config import Settings, get_settings
from service.cloudwatch_service import CloudWatchService
from service.database_service import DatabaseService
from service.newrelic_service import NewRelicService


class Container:
    """Composition root: the single place that knows how to build every
    service and wire its dependencies. Tool functions ask the container for
    a service (`get_container().database_service`) instead of constructing
    one themselves — that's the whole point of dependency inversion here:
    `tools/*` depend on "a container that hands out services", not on the
    concrete `DatabaseService`/`CloudWatchService`/`NewRelicService`
    constructors scattered across sixteen files.

    Every dependency can also be injected directly through the constructor
    (a pre-built service, an override `Settings`), which is how tests that
    want a full request through a service without touching the real AWS/New
    Relic/DB clients can build an isolated `Container` instead of
    monkeypatching. Unit tests for an individual service still construct
    that service directly (see `tests/unit/test_cloudwatch_tools.py`) — the
    container exists for wiring the production object graph, not to
    replace the simpler constructor-injection seam each service already
    has.

    Services are built lazily and cached for the container's lifetime
    (a light singleton scope), so `get_container().database_service` inside
    one request and the next reuse the same instance instead of
    re-constructing it per tool call.
    """

    def __init__(
        self,
        settings: Settings | None = None,
        database_service: DatabaseService | None = None,
        cloudwatch_service: CloudWatchService | None = None,
        newrelic_service: NewRelicService | None = None,
    ) -> None:
        self._settings_override = settings
        self._database_service = database_service
        self._cloudwatch_service = cloudwatch_service
        self._newrelic_service = newrelic_service

    @property
    def settings(self) -> Settings:
        return self._settings_override if self._settings_override is not None else get_settings()

    @property
    def database_service(self) -> DatabaseService:
        if self._database_service is None:
            self._database_service = DatabaseService(self.settings)
        return self._database_service

    @property
    def cloudwatch_service(self) -> CloudWatchService:
        if self._cloudwatch_service is None:
            self._cloudwatch_service = CloudWatchService(self.settings)
        return self._cloudwatch_service

    @property
    def newrelic_service(self) -> NewRelicService:
        if self._newrelic_service is None:
            self._newrelic_service = NewRelicService(self.settings)
        return self._newrelic_service


_container: Container | None = None


def get_container() -> Container:
    """Process-wide container accessor. Tool functions call this instead of
    constructing a `Container` (or a service) themselves."""
    global _container
    if _container is None:
        _container = Container()
    return _container


def reset_container() -> None:
    """Drop the cached container so the next `get_container()` call rebuilds
    services against current `Settings` — call this after
    `get_settings.cache_clear()` in any test that routes through the
    container, mirroring `integrations.database.engine.reset_engine()`."""
    global _container
    _container = None
