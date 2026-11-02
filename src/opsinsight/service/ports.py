"""The interfaces services depend on (dependency inversion).

Services type their injected clients against these Protocols instead of the
concrete boto3 / httpx / SQLAlchemy objects, so a fake that implements the
same methods can be passed in tests. Only the methods services actually call
are listed (interface segregation).
"""

from collections.abc import Awaitable, Callable, Iterable
from typing import Any, Protocol

from sqlalchemy.ext.asyncio import AsyncEngine

from opsinsight.core.config import Settings
from opsinsight.integrations.database.models import (
    IdentifierSearchResult,
    SampleRowsResult,
    StoreResolutionResult,
    TableDescription,
    TableSummary,
)


class Paginator(Protocol):
    def paginate(self, **kwargs: Any) -> Iterable[dict[str, Any]]: ...


class LogsClient(Protocol):
    """The subset of boto3's CloudWatch Logs client the services use."""

    def get_paginator(self, operation_name: str) -> Paginator: ...
    def filter_log_events(self, **kwargs: Any) -> dict[str, Any]: ...
    def start_query(self, **kwargs: Any) -> dict[str, Any]: ...
    def get_query_results(self, *, queryId: str) -> dict[str, Any]: ...
    def stop_query(self, *, queryId: str) -> dict[str, Any]: ...


class MetricsClient(Protocol):
    """The subset of boto3's CloudWatch client the services use."""

    def get_metric_statistics(self, **kwargs: Any) -> dict[str, Any]: ...


LogsClientFactory = Callable[[Settings], LogsClient]
MetricsClientFactory = Callable[[Settings], MetricsClient]

# Runs one NRQL query; returns {"results": [...], "metadata": {...}}.
NrqlRunner = Callable[[str, Settings], Awaitable[dict[str, Any]]]

EngineFactory = Callable[[Settings], AsyncEngine]


class DatabaseCatalog(Protocol):
    """Catalog lookups `DatabaseService` needs. The default implementation is
    the `integrations.database.introspect` module."""

    async def list_tables(self, schema: str | None, settings: Settings | None = None) -> list[TableSummary]: ...
    async def describe_table(self, table: str, settings: Settings | None = None) -> TableDescription: ...
    async def sample_rows(self, table: str, limit: int, settings: Settings | None = None) -> SampleRowsResult: ...
    async def search_by_identifier(
        self, identifier: str, id_type: str, settings: Settings | None = None, limit: int | None = None
    ) -> IdentifierSearchResult: ...
    async def resolve_store(
        self, name_or_domain: str, settings: Settings | None = None
    ) -> StoreResolutionResult: ...
