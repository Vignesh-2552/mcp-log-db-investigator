import asyncio
from dataclasses import dataclass
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError

from core.cache import ttl_cache
from core.config import Settings, get_settings
from core.errors import GuardrailError
from core.logging_config import get_logger
from core.redaction import redact_rows
from integrations.database.constants import MAX_SEARCH_TARGETS, STORE_ID_KEYS, TLD_RE
from integrations.database.engine import get_engine
from integrations.database.guardrail import sqlalchemy_error_detail, validate_sql
from integrations.database.models import (
    Column,
    ForeignKey,
    IdentifierMatch,
    IdentifierSearchResult,
    IdentifierSkip,
    Index,
    SampleRowsResult,
    SearchedTable,
    StoreCandidate,
    StoreResolutionResult,
    StoreSkip,
    TableDescription,
    TableSummary,
)
from integrations.database.queries import (
    COLUMN_SEARCH_SQL,
    ENTITY_TABLE_SQL,
    LIST_TABLES_SQL,
    STORE_COLUMN_SEARCH_SQL,
    build_identifier_lookup_sql,
    build_sample_rows_sql,
    build_store_lookup_sql,
)

logger = get_logger("database.introspect")


def _split_table(table: str) -> tuple[str | None, str]:
    """Splits 'schema.table' into (schema, table). An unqualified name
    returns (None, table) — no forced 'public' default — so it resolves
    via the DB's own search_path rather than assuming a single schema."""
    if "." in table:
        schema, name = table.split(".", 1)
        return schema, name
    return None, table


def _qualify(schema: str | None, name: str) -> str:
    return f"{schema}.{name}".lower() if schema else name.lower()


def _sync_describe_table(sync_conn, name: str, schema: str | None) -> dict[str, Any]:
    inspector = inspect(sync_conn)
    return {
        "columns": inspector.get_columns(name, schema=schema),
        "primary_key": inspector.get_pk_constraint(name, schema=schema),
        "foreign_keys": inspector.get_foreign_keys(name, schema=schema),
        "indexes": inspector.get_indexes(name, schema=schema),
    }


@ttl_cache(maxsize=8, ttl_seconds=lambda: get_settings().db_schema_cache_ttl_s)
async def _list_tables_cached(schema: str | None) -> list[TableSummary]:
    """One round-trip regardless of table count (was 2 * N round-trips
    before — fine on local Postgres, painfully slow over a real network
    to a remote DB with dozens/hundreds of tables)."""
    settings = get_settings()
    engine = get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(text(LIST_TABLES_SQL), {"schema": schema})
        rows = result.mappings().all()
    return [
        TableSummary(
            table=f"{row['schema']}.{row['name']}".lower(),
            row_estimate=int(row["estimate"]) if row["estimate"] is not None else None,
            comment=row["comment"],
        )
        for row in rows
    ]


async def list_tables(schema: str | None) -> list[TableSummary]:
    """Cached for the configured DB_SCHEMA_CACHE_TTL_S."""
    return await _list_tables_cached(schema)


async def describe_table(table: str) -> TableDescription:
    schema, name = _split_table(table)
    qualified = _qualify(schema, name)
    engine = get_engine()
    async with engine.connect() as conn:
        detail = await conn.run_sync(_sync_describe_table, name, schema)
    pk = detail["primary_key"]
    return TableDescription(
        table=qualified,
        columns=[
            Column(
                name=c["name"],
                type=str(c["type"]),
                nullable=c["nullable"],
                default=str(c["default"]) if c.get("default") is not None else None,
            )
            for c in detail["columns"]
        ],
        primary_key=pk.get("constrained_columns", []),
        foreign_keys=[
            ForeignKey(
                columns=fk["constrained_columns"],
                references_table=_qualify(fk.get("referred_schema") or schema, fk["referred_table"]),
                references_columns=fk["referred_columns"],
            )
            for fk in detail["foreign_keys"]
        ],
        indexes=[
            Index(name=idx["name"], columns=idx["column_names"], unique=idx["unique"])
            for idx in detail["indexes"]
        ],
    )


async def sample_rows(table: str, limit: int, settings: Settings | None = None) -> SampleRowsResult:
    schema, name = _split_table(table)
    qualified = _qualify(schema, name)
    settings = settings or get_settings()
    clamped_limit = max(1, min(limit, settings.db_max_rows))
    engine = get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(
            text(build_sample_rows_sql(schema, name)), {"limit": clamped_limit}
        )
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    rows = redact_rows(rows, settings)
    return SampleRowsResult(table=qualified, columns=columns, rows=rows, row_count=len(rows))


# db_search_by_identifier — the "find order X" convenience flow.
# No hardcoded table/schema names: id_type is resolved against the DB's own
# catalog at call time, so this works against any project's schema without
# code changes. Two ways a match is found for e.g. id_type="order_id":
#   1. Any column literally named "order_id" anywhere in the DB (exact
#      catalog match — cheap, precise, no false positives).
#   2. The entity's own table (singular/plural of "order" -> "order"/
#      "orders") searched by its own single-column primary key. Exact
#      table-name match only (not substring), so the hundreds of
#      "migration.*_order" style tables never match.


def _entity_name_candidates(id_type: str) -> list[str]:
    """order_id -> ['order', 'orders']; category -> ['categories', 'category']."""
    entity = id_type.removesuffix("_id")
    if not entity:
        return []
    candidates = {entity, f"{entity}s"}
    if entity.endswith("y") and not entity.endswith(("ay", "ey", "iy", "oy", "uy")):
        candidates.add(f"{entity[:-1]}ies")
    return sorted(candidates)


@ttl_cache(maxsize=64, ttl_seconds=lambda: get_settings().db_schema_cache_ttl_s)
async def _find_tables_with_column(column_name: str) -> list[tuple[str, str]]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(COLUMN_SEARCH_SQL), {"column_name": column_name})
        return [(row.schema, row.table_name) for row in result]


@ttl_cache(maxsize=64, ttl_seconds=lambda: get_settings().db_schema_cache_ttl_s)
async def _find_entity_tables(entity_candidates: tuple[str, ...]) -> list[tuple[str, str, str]]:
    if not entity_candidates:
        return []
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(ENTITY_TABLE_SQL), {"table_names": list(entity_candidates)})
        return [(row.schema, row.table_name, row.pk_column) for row in result]


def _is_historical_schema(schema: str, prefixes: frozenset[str]) -> bool:
    return any(schema.lower().startswith(p.lower()) for p in prefixes)


def _source_type(schema: str, prefixes: frozenset[str]) -> str:
    return "historical" if _is_historical_schema(schema, prefixes) else "live"


@dataclass
class _Probe:
    """Internal-only working shape shared by search_by_identifier's and
    resolve_store's per-target `_search_one` closures — never returned to a
    caller. `source_type` is only populated by search_by_identifier (resolve_
    store has no historical/live distinction), so it's left None there and
    simply not read when building StoreCandidate/StoreSkip."""

    table: str
    column: str
    skipped: bool
    reason: str | None = None
    rows: list[dict[str, Any]] | None = None
    row_count: int | None = None
    source_type: str | None = None


async def search_by_identifier(
    identifier: str, id_type: str, settings: Settings | None = None
) -> IdentifierSearchResult:
    settings = settings or get_settings()
    historical_prefixes = settings.db_historical_schema_prefixes_set

    column_matches, entity_matches = await asyncio.gather(
        _find_tables_with_column(id_type),
        _find_entity_tables(tuple(_entity_name_candidates(id_type))),
    )

    # Entity matches first — e.g. for id_type="order_id" the "orders" table
    # itself (the actual entity) matters more than the dozens of unrelated
    # tables that merely have a foreign-key column named "order_id", and
    # must not get pushed out by the MAX_SEARCH_TARGETS cap. Within each
    # rank, live-schema tables sort ahead of historical/migration-snapshot
    # ones so a plain alphabetical schema-name ordering (e.g. "migration" <
    # "public") can't starve live tables out of the cap below.
    ranked: list[tuple[int, bool, str, str, str]] = []  # (rank, is_historical, schema, table, column)
    seen: set[tuple[str, str, str]] = set()
    for schema, table, pk_column in entity_matches:
        key = (schema, table, pk_column)
        if key not in seen:
            seen.add(key)
            ranked.append((0, _is_historical_schema(schema, historical_prefixes), schema, table, pk_column))
    for schema, table in column_matches:
        key = (schema, table, id_type)
        if key not in seen:
            seen.add(key)
            ranked.append((1, _is_historical_schema(schema, historical_prefixes), schema, table, id_type))

    if not ranked:
        raise GuardrailError(
            rule="no_matching_tables",
            message=f"No column named '{id_type}' and no table matching that entity name were found.",
            detail="Try db_list_tables or db_describe_table to find the right column name.",
        )

    ranked.sort(key=lambda r: r[:4])
    targets: list[tuple[str, str, str]] = [(s, t, c) for _, _, s, t, c in ranked]

    truncated = len(targets) > MAX_SEARCH_TARGETS
    targets = targets[:MAX_SEARCH_TARGETS]

    engine = get_engine(settings)

    async def _search_one(schema: str, table: str, column: str) -> _Probe:
        qualified = f"{schema}.{table}"
        source_type = _source_type(schema, historical_prefixes)
        raw_sql = build_identifier_lookup_sql(schema, table, column)
        # Even though schema/table/column come from the DB's own catalog
        # (not directly from the caller), route the generated SQL through
        # the same guardrail every other query goes through — defense in
        # depth per CLAUDE.md. Only `validated.limit` is used, not
        # `validated.sql`: sqlglot's postgres-dialect re-serialization
        # rewrites named bind params (`:identifier`) into pyformat markers
        # (`%(identifier)s`), which SQLAlchemy's `text()` then can't bind —
        # so execution keeps the original `:identifier`-parameterized SQL
        # and only borrows the guardrail's clamped row limit as a literal.
        try:
            validated = validate_sql(raw_sql, max_rows=settings.db_max_rows)
            executable_sql = f"{raw_sql} LIMIT {validated.limit}"
            async with engine.connect() as conn:
                result = await conn.execute(text(executable_sql), {"identifier": identifier})
                columns = list(result.keys())
                rows = [dict(zip(columns, row)) for row in result.fetchall()]
        except DataError as e:
            # The identifier's literal shape doesn't fit this column's SQL
            # type (e.g. a non-UUID string against a uuid PK) — since
            # id_type is resolved dynamically across every plausibly-matching
            # table/column, this is a normal "not a match" outcome for one
            # target, not a reason to fail the whole fan-out. Only DataError
            # is caught here (not SQLAlchemyError broadly) so a real DB
            # failure on this target — a timeout, a dropped connection —
            # still propagates and surfaces as a proper error instead of
            # silently reading as "no match".
            detail = sqlalchemy_error_detail(e)
            logger.debug("Skipping %s.%s for identifier search: %s", qualified, column, detail)
            return _Probe(table=qualified, column=column, skipped=True, reason=detail, source_type=source_type)
        except GuardrailError as e:
            # The generated SQL failed the same validate_sql guardrail every
            # other query goes through — treat as "not a usable target"
            # rather than failing the whole fan-out over one candidate.
            logger.debug("Skipping %s.%s for identifier search: guardrail rejected: %s", qualified, column, e)
            return _Probe(table=qualified, column=column, skipped=True, reason=str(e), source_type=source_type)
        if not rows:
            return _Probe(table=qualified, column=column, skipped=False, row_count=0, source_type=source_type)
        return _Probe(
            table=qualified,
            column=column,
            skipped=False,
            rows=redact_rows(rows, settings),
            row_count=len(rows),
            source_type=source_type,
        )

    # Each target gets its own pooled connection, run concurrently — with
    # up to MAX_SEARCH_TARGETS tables to check, doing this serially would
    # scale linearly with per-query network latency (25 tables x ~1s+ each
    # over a real network is a genuinely bad wait).
    probes = await asyncio.gather(*(_search_one(s, t, c) for s, t, c in targets))
    matches = [
        IdentifierMatch(table=p.table, column=p.column, source_type=p.source_type, rows=p.rows, row_count=p.row_count)
        for p in probes
        if not p.skipped and p.row_count
    ]
    skipped = [
        IdentifierSkip(table=p.table, column=p.column, reason=p.reason, source_type=p.source_type)
        for p in probes
        if p.skipped
    ]

    all_searched_historical = bool(targets) and all(
        _is_historical_schema(s, historical_prefixes) for s, _, _ in targets
    )
    all_matches_historical = bool(matches) and all(m.source_type == "historical" for m in matches)
    data_freshness_note = None
    if all_searched_historical:
        data_freshness_note = (
            "All searched tables are in historical/migration-snapshot schema(s) "
            f"(prefix: {settings.db_historical_schema_prefixes}); a 'no matches' result "
            "does not confirm the identifier doesn't exist in live data."
        )
    elif all_matches_historical:
        data_freshness_note = (
            "All matches found are in historical/migration-snapshot schema(s); results may "
            "reflect stale/migrated data, not the live system."
        )

    return IdentifierSearchResult(
        identifier=identifier,
        id_type=id_type,
        searched_tables=[
            SearchedTable(table=f"{s}.{t}", source_type=_source_type(s, historical_prefixes)) for s, t, _ in targets
        ],
        truncated=truncated,
        matches=matches,
        skipped=skipped,
        data_freshness_note=data_freshness_note,
    )


# db_resolve_store — resolve a store name/domain to its store_id. No
# hardcoded "stores" table: candidate columns (domain/hostname/store_name/...,
# configurable via DB_STORE_IDENTIFIER_COLUMNS) are discovered from the DB's
# own catalog at call time, then matched with ILIKE (not exact "=") since a
# domain like "olallawines.com" won't exact-match a stored display name or a
# bare hostname without a TLD. When candidates resolve to more than one
# distinct store_id, the response is `ambiguous: true` rather than silently
# picking one.


def _domain_search_variants(name_or_domain: str) -> list[str]:
    """'olallawines.com' -> ['olallawines.com', 'olallawines'] so a bare-
    hostname-stored value (no TLD) still matches. Doesn't attempt display-name
    normalization (e.g. "Olalla Wines" vs "olallawines.com")."""
    bare = TLD_RE.sub("", name_or_domain)
    return [name_or_domain] if bare == name_or_domain else [name_or_domain, bare]


@ttl_cache(maxsize=8, ttl_seconds=lambda: get_settings().db_schema_cache_ttl_s)
async def _find_store_identifier_columns(column_names: tuple[str, ...]) -> list[tuple[str, str, str]]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(STORE_COLUMN_SEARCH_SQL), {"column_names": list(column_names)})
        return [(row.schema, row.table_name, row.column_name) for row in result]


async def resolve_store(name_or_domain: str, settings: Settings | None = None) -> StoreResolutionResult:
    settings = settings or get_settings()
    columns = tuple(sorted(settings.db_store_identifier_columns_set))
    targets = await _find_store_identifier_columns(columns)

    if not targets:
        raise GuardrailError(
            rule="no_store_identifier_columns",
            message="No columns matching known store/domain identifier names were found in the catalog.",
            detail=(
                f"Looked for: {', '.join(columns)}. Configure DB_STORE_IDENTIFIER_COLUMNS "
                "if the real column names differ."
            ),
        )

    truncated = len(targets) > MAX_SEARCH_TARGETS
    targets = targets[:MAX_SEARCH_TARGETS]
    variants = _domain_search_variants(name_or_domain)
    engine = get_engine(settings)

    async def _search_one(schema: str, table: str, column: str) -> _Probe:
        qualified = f"{schema}.{table}"
        raw_sql = build_store_lookup_sql(schema, table, column)
        # Same rationale as search_by_identifier: route the generated SQL
        # through the guardrail (defense in depth), but keep the original
        # `:pattern`-parameterized text for execution — see the comment in
        # search_by_identifier's _search_one for why `validated.sql` itself
        # isn't usable here.
        try:
            validated = validate_sql(raw_sql, max_rows=settings.db_max_rows)
            executable_sql = f"{raw_sql} LIMIT {validated.limit}"
            rows: list[dict[str, Any]] = []
            seen_keys: set[tuple[str, ...]] = set()
            async with engine.connect() as conn:
                for variant in variants:
                    result = await conn.execute(text(executable_sql), {"pattern": f"%{variant}%"})
                    cols = list(result.keys())
                    for row in result.fetchall():
                        row_dict = dict(zip(cols, row))
                        key = tuple(str(v) for v in row_dict.values())
                        if key not in seen_keys:
                            seen_keys.add(key)
                            rows.append(row_dict)
        except DataError as e:
            # Same rationale as search_by_identifier's _search_one: the
            # column's SQL type not accepting a text ILIKE pattern is a
            # normal "not a match" outcome for this one target.
            detail = sqlalchemy_error_detail(e)
            logger.debug("Skipping %s.%s for store resolution: %s", qualified, column, detail)
            return _Probe(table=qualified, column=column, skipped=True, reason=detail)
        except GuardrailError as e:
            logger.debug("Skipping %s.%s for store resolution: guardrail rejected: %s", qualified, column, e)
            return _Probe(table=qualified, column=column, skipped=True, reason=str(e))
        if not rows:
            return _Probe(table=qualified, column=column, skipped=False, row_count=0)
        return _Probe(
            table=qualified,
            column=column,
            skipped=False,
            rows=redact_rows(rows, settings),
            row_count=len(rows),
        )

    probes = await asyncio.gather(*(_search_one(s, t, c) for s, t, c in targets))
    matches = [p for p in probes if not p.skipped and p.row_count]
    skipped = [StoreSkip(table=p.table, column=p.column, reason=p.reason) for p in probes if p.skipped]

    candidates: list[StoreCandidate] = []
    for m in matches:
        for row in m.rows:
            store_id = next((row[k] for k in STORE_ID_KEYS if k in row), None)
            candidates.append(StoreCandidate(table=m.table, matched_column=m.column, store_id=store_id, row=row))

    # Dedup by string form, not raw value: different tables can store the
    # same store_id as an int in one and text in another (e.g. a legacy/
    # migrated table), and naive set-equality over raw values would treat
    # 123 and '123' as two distinct stores instead of recognizing the
    # unambiguous match. The first-seen raw value for each normalized key
    # is kept as the value to return.
    distinct_by_str: dict[str, Any] = {}
    for c in candidates:
        if c.store_id is not None:
            distinct_by_str.setdefault(str(c.store_id), c.store_id)
    ambiguous = len(distinct_by_str) > 1
    note = None
    if candidates and not distinct_by_str:
        note = (
            "Matches found but no obvious store_id/id column in the matched rows; "
            "inspect candidates[].row manually."
        )

    return StoreResolutionResult(
        name_or_domain=name_or_domain,
        searched_columns=[f"{s}.{t}.{c}" for s, t, c in targets],
        truncated=truncated,
        ambiguous=ambiguous,
        store_id=next(iter(distinct_by_str.values())) if (distinct_by_str and not ambiguous) else None,
        candidates=candidates,
        skipped=skipped,
        note=note,
    )
