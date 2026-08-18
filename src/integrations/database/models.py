from dataclasses import dataclass
from typing import Any

from core.models import DictableMixin


@dataclass
class Column(DictableMixin):
    name: str
    type: str
    nullable: bool
    default: str | None


@dataclass
class ForeignKey(DictableMixin):
    columns: list[str]
    references_table: str
    references_columns: list[str]


@dataclass
class Index(DictableMixin):
    name: str
    columns: list[str]
    unique: bool


@dataclass
class TableDescription(DictableMixin):
    table: str
    columns: list[Column]
    primary_key: list[str]
    foreign_keys: list[ForeignKey]
    indexes: list[Index]


@dataclass
class TableSummary(DictableMixin):
    table: str
    row_estimate: int | None
    comment: str | None


@dataclass
class SampleRowsResult(DictableMixin):
    table: str
    columns: list[str]
    rows: list[dict[str, Any]]
    row_count: int


@dataclass
class SearchedTable(DictableMixin):
    table: str
    source_type: str


@dataclass
class IdentifierMatch(DictableMixin):
    table: str
    column: str
    source_type: str
    rows: list[dict[str, Any]]
    row_count: int


@dataclass
class IdentifierSkip(DictableMixin):
    table: str
    column: str
    reason: str
    source_type: str


@dataclass
class IdentifierSearchResult(DictableMixin):
    identifier: str
    id_type: str
    searched_tables: list[SearchedTable]
    truncated: bool
    matches: list[IdentifierMatch]
    skipped: list[IdentifierSkip]
    data_freshness_note: str | None


@dataclass
class StoreCandidate(DictableMixin):
    table: str
    matched_column: str
    store_id: Any
    row: dict[str, Any]


@dataclass
class StoreSkip(DictableMixin):
    table: str
    column: str
    reason: str


@dataclass
class StoreResolutionResult(DictableMixin):
    name_or_domain: str
    searched_columns: list[str]
    truncated: bool
    ambiguous: bool
    store_id: Any
    candidates: list[StoreCandidate]
    skipped: list[StoreSkip]
    note: str | None
