from opsinsight.core.config import Settings, get_settings
from opsinsight.service.cloudwatch_logs_service import CloudWatchLogsService
from opsinsight.service.cloudwatch_metrics_service import CloudWatchMetricsService
from opsinsight.service.database_service import DatabaseService
from opsinsight.service.newrelic_service import NewRelicService
from opsinsight.service.trace_service import TraceService
from opsinsight.service.trace_sources import default_trace_sources


class Container:
    """Composition root: the single place that knows how to build every
    service and wire its dependencies. Tool functions ask the container for
    a service (`get_container().database_service`) instead of constructing
    one themselves — that's the whole point of dependency inversion here:
    `tools/*` depend on "a container that hands out services", not on the
    concrete service constructors scattered across every tool file.

    Every dependency can also be injected directly through the constructor
    (a pre-built service, an override `Settings`), which is how tests that
    want a full request through a service without touching the real AWS/New
    Relic/DB clients can build an isolated `Container` instead of
    monkeypatching. Unit tests for an individual service still construct
    that service directly (see `service/cloudwatch_logs_service_test.py`) — the
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
        cloudwatch_logs_service: CloudWatchLogsService | None = None,
        cloudwatch_metrics_service: CloudWatchMetricsService | None = None,
        newrelic_service: NewRelicService | None = None,
        trace_service: TraceService | None = None,
    ) -> None:
        self._settings_override = settings
        self._database_service = database_service
        self._cloudwatch_logs_service = cloudwatch_logs_service
        self._cloudwatch_metrics_service = cloudwatch_metrics_service
        self._newrelic_service = newrelic_service
        self._trace_service = trace_service

    @property
    def settings(self) -> Settings:
        return self._settings_override if self._settings_override is not None else get_settings()

    @property
    def database_service(self) -> DatabaseService:
        if self._database_service is None:
            self._database_service = DatabaseService(self.settings)
        return self._database_service

    @property
    def cloudwatch_logs_service(self) -> CloudWatchLogsService:
        if self._cloudwatch_logs_service is None:
            self._cloudwatch_logs_service = CloudWatchLogsService(self.settings)
        return self._cloudwatch_logs_service

    @property
    def cloudwatch_metrics_service(self) -> CloudWatchMetricsService:
        if self._cloudwatch_metrics_service is None:
            self._cloudwatch_metrics_service = CloudWatchMetricsService(self.settings)
        return self._cloudwatch_metrics_service

    @property
    def newrelic_service(self) -> NewRelicService:
        if self._newrelic_service is None:
            self._newrelic_service = NewRelicService(self.settings)
        return self._newrelic_service

    @property
    def trace_service(self) -> TraceService:
        # Built from the other three, so injected fakes flow through to it.
        if self._trace_service is None:
            self._trace_service = TraceService(
                self.settings,
                default_trace_sources(
                    self.settings,
                    database_service=self.database_service,
                    cloudwatch_logs_service=self.cloudwatch_logs_service,
                    newrelic_service=self.newrelic_service,
                ),
            )
        return self._trace_service


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
