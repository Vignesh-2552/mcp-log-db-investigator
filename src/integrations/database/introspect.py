import asyncio
import re
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.exc import DataError

from core.cache import ttl_cache
from core.config import Settings, get_settings
from core.errors import GuardrailError
from core.logging_config import get_logger
from core.redaction import redact_rows
from integrations.database.engine import get_engine
from integrations.database.guardrail import sqlalchemy_error_detail

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


_LIST_TABLES_SQL = """\
SELECT n.nspname AS schema, c.relname AS name,
       c.reltuples::bigint AS estimate, obj_description(c.oid) AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND (CAST(:schema AS text) IS NULL OR n.nspname = CAST(:schema AS text))
ORDER BY n.nspname, c.relname
"""


@ttl_cache(maxsize=8, ttl_seconds=600)
async def _list_tables_cached(schema: str | None) -> list[dict[str, Any]]:
    """One round-trip regardless of table count (was 2 * N round-trips
    before — fine on local Postgres, painfully slow over a real network
    to a remote DB with dozens/hundreds of tables)."""
    settings = get_settings()
    engine = get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(text(_LIST_TABLES_SQL), {"schema": schema})
        rows = result.mappings().all()
    return [
        {
            "table": f"{row['schema']}.{row['name']}".lower(),
            "row_estimate": int(row["estimate"]) if row["estimate"] is not None else None,
            "comment": row["comment"],
        }
        for row in rows
    ]


async def list_tables(schema: str | None) -> list[dict[str, Any]]:
    """Cached ~10min."""
    return await _list_tables_cached(schema)


async def describe_table(table: str) -> dict[str, Any]:
    schema, name = _split_table(table)
    qualified = _qualify(schema, name)
    engine = get_engine()
    async with engine.connect() as conn:
        detail = await conn.run_sync(_sync_describe_table, name, schema)
    pk = detail["primary_key"]
    return {
        "table": qualified,
        "columns": [
            {
                "name": c["name"],
                "type": str(c["type"]),
                "nullable": c["nullable"],
                "default": str(c["default"]) if c.get("default") is not None else None,
            }
            for c in detail["columns"]
        ],
        "primary_key": pk.get("constrained_columns", []),
        "foreign_keys": [
            {
                "columns": fk["constrained_columns"],
                "references_table": _qualify(fk.get("referred_schema") or schema, fk["referred_table"]),
                "references_columns": fk["referred_columns"],
            }
            for fk in detail["foreign_keys"]
        ],
        "indexes": [
            {"name": idx["name"], "columns": idx["column_names"], "unique": idx["unique"]}
            for idx in detail["indexes"]
        ],
    }


async def sample_rows(table: str, limit: int, settings: Settings | None = None) -> dict[str, Any]:
    schema, name = _split_table(table)
    qualified = _qualify(schema, name)
    settings = settings or get_settings()
    clamped_limit = max(1, min(limit, settings.db_max_rows))
    engine = get_engine(settings)
    async with engine.connect() as conn:
        result = await conn.execute(text(f"SELECT * FROM {qualified} LIMIT :limit"), {"limit": clamped_limit})
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    rows = redact_rows(rows, settings)
    return {"table": qualified, "columns": columns, "rows": rows, "row_count": len(rows)}


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
_MAX_SEARCH_TARGETS = 25

_COLUMN_SEARCH_SQL = """\
SELECT n.nspname AS schema, c.relname AS table_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND a.attname = :column_name
ORDER BY n.nspname, c.relname
"""

_ENTITY_TABLE_SQL = """\
SELECT n.nspname AS schema, c.relname AS table_name, a.attname AS pk_column
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE i.indisprimary
  AND c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND c.relname = ANY(:table_names)
  AND cardinality(i.indkey) = 1
"""


def _entity_name_candidates(id_type: str) -> list[str]:
    """order_id -> ['order', 'orders']; category -> ['categories', 'category']."""
    entity = id_type[:-3] if id_type.endswith("_id") else id_type
    if not entity:
        return []
    candidates = {entity, f"{entity}s"}
    if entity.endswith("y") and not entity.endswith(("ay", "ey", "iy", "oy", "uy")):
        candidates.add(f"{entity[:-1]}ies")
    return sorted(candidates)


@ttl_cache(maxsize=64, ttl_seconds=600)
async def _find_tables_with_column(column_name: str) -> list[tuple[str, str]]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(_COLUMN_SEARCH_SQL), {"column_name": column_name})
        return [(row.schema, row.table_name) for row in result]


@ttl_cache(maxsize=64, ttl_seconds=600)
async def _find_entity_tables(entity_candidates: tuple[str, ...]) -> list[tuple[str, str, str]]:
    if not entity_candidates:
        return []
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(_ENTITY_TABLE_SQL), {"table_names": list(entity_candidates)})
        return [(row.schema, row.table_name, row.pk_column) for row in result]


def _is_historical_schema(schema: str, prefixes: frozenset[str]) -> bool:
    return any(schema.lower().startswith(p.lower()) for p in prefixes)


def _source_type(schema: str, prefixes: frozenset[str]) -> str:
    return "historical" if _is_historical_schema(schema, prefixes) else "live"


async def search_by_identifier(
    identifier: str, id_type: str, settings: Settings | None = None
) -> dict[str, Any]:
    settings = settings or get_settings()
    historical_prefixes = settings.db_historical_schema_prefixes_set

    column_matches = await _find_tables_with_column(id_type)
    entity_matches = await _find_entity_tables(tuple(_entity_name_candidates(id_type)))

    # Entity matches first — e.g. for id_type="order_id" the "orders" table
    # itself (the actual entity) matters more than the dozens of unrelated
    # tables that merely have a foreign-key column named "order_id", and
    # must not get pushed out by the _MAX_SEARCH_TARGETS cap. Within each
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

    truncated = len(targets) > _MAX_SEARCH_TARGETS
    targets = targets[:_MAX_SEARCH_TARGETS]

    engine = get_engine(settings)

    async def _search_one(schema: str, table: str, column: str) -> dict[str, Any]:
        qualified = f"{schema}.{table}"
        try:
            async with engine.connect() as conn:
                result = await conn.execute(
                    text(f'SELECT * FROM "{schema}"."{table}" WHERE "{column}" = :identifier LIMIT :limit'),
                    {"identifier": identifier, "limit": settings.db_max_rows},
                )
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
            return {"table": qualified, "column": column, "skipped": True, "reason": detail}
        if not rows:
            return {"table": qualified, "column": column, "skipped": False, "row_count": 0}
        return {
            "table": qualified,
            "column": column,
            "skipped": False,
            "rows": redact_rows(rows, settings),
            "row_count": len(rows),
        }

    # Each target gets its own pooled connection, run concurrently — with
    # up to _MAX_SEARCH_TARGETS tables to check, doing this serially would
    # scale linearly with per-query network latency (25 tables x ~1s+ each
    # over a real network is a genuinely bad wait).
    results = await asyncio.gather(*(_search_one(s, t, c) for s, t, c in targets))
    matches = [r for r in results if not r["skipped"] and r["row_count"] > 0]
    skipped = [
        {"table": r["table"], "column": r["column"], "reason": r["reason"]}
        for r in results
        if r["skipped"]
    ]
    for r in (*matches, *skipped):
        r["source_type"] = _source_type(r["table"].split(".", 1)[0], historical_prefixes)

    all_searched_historical = bool(targets) and all(
        _is_historical_schema(s, historical_prefixes) for s, _, _ in targets
    )
    all_matches_historical = bool(matches) and all(m["source_type"] == "historical" for m in matches)
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

    return {
        "identifier": identifier,
        "id_type": id_type,
        "searched_tables": [
            {"table": f"{s}.{t}", "source_type": _source_type(s, historical_prefixes)} for s, t, _ in targets
        ],
        "truncated": truncated,
        "matches": matches,
        "skipped": skipped,
        "data_freshness_note": data_freshness_note,
    }


# db_resolve_store — resolve a store name/domain to its store_id. No
# hardcoded "stores" table: candidate columns (domain/hostname/store_name/...,
# configurable via DB_STORE_IDENTIFIER_COLUMNS) are discovered from the DB's
# own catalog at call time, then matched with ILIKE (not exact "=") since a
# domain like "olallawines.com" won't exact-match a stored display name or a
# bare hostname without a TLD. When candidates resolve to more than one
# distinct store_id, the response is `ambiguous: true` rather than silently
# picking one.
_STORE_ID_KEYS = ("store_id", "id")
_TLD_RE = re.compile(r"\.[a-zA-Z]{2,}$")

_STORE_COLUMN_SEARCH_SQL = """\
SELECT n.nspname AS schema, c.relname AS table_name, a.attname AS column_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND a.attname = ANY(:column_names)
ORDER BY n.nspname, c.relname
"""


def _domain_search_variants(name_or_domain: str) -> list[str]:
    """'olallawines.com' -> ['olallawines.com', 'olallawines'] so a bare-
    hostname-stored value (no TLD) still matches. Doesn't attempt display-name
    normalization (e.g. "Olalla Wines" vs "olallawines.com")."""
    bare = _TLD_RE.sub("", name_or_domain)
    return [name_or_domain] if bare == name_or_domain else [name_or_domain, bare]


@ttl_cache(maxsize=8, ttl_seconds=600)
async def _find_store_identifier_columns(column_names: tuple[str, ...]) -> list[tuple[str, str, str]]:
    engine = get_engine()
    async with engine.connect() as conn:
        result = await conn.execute(text(_STORE_COLUMN_SEARCH_SQL), {"column_names": list(column_names)})
        return [(row.schema, row.table_name, row.column_name) for row in result]


async def resolve_store(name_or_domain: str, settings: Settings | None = None) -> dict[str, Any]:
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

    truncated = len(targets) > _MAX_SEARCH_TARGETS
    targets = targets[:_MAX_SEARCH_TARGETS]
    variants = _domain_search_variants(name_or_domain)
    engine = get_engine(settings)

    async def _search_one(schema: str, table: str, column: str) -> dict[str, Any]:
        qualified = f"{schema}.{table}"
        try:
            rows: list[dict[str, Any]] = []
            seen_keys: set[tuple[str, ...]] = set()
            async with engine.connect() as conn:
                for variant in variants:
                    result = await conn.execute(
                        text(f'SELECT * FROM "{schema}"."{table}" WHERE "{column}" ILIKE :pattern LIMIT :limit'),
                        {"pattern": f"%{variant}%", "limit": settings.db_max_rows},
                    )
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
            return {"table": qualified, "column": column, "skipped": True, "reason": detail}
        if not rows:
            return {"table": qualified, "column": column, "skipped": False, "row_count": 0}
        return {
            "table": qualified,
            "column": column,
            "skipped": False,
            "rows": redact_rows(rows, settings),
            "row_count": len(rows),
        }

    results = await asyncio.gather(*(_search_one(s, t, c) for s, t, c in targets))
    matches = [r for r in results if not r["skipped"] and r["row_count"] > 0]
    skipped = [
        {"table": r["table"], "column": r["column"], "reason": r["reason"]}
        for r in results
        if r["skipped"]
    ]

    candidates: list[dict[str, Any]] = []
    for m in matches:
        for row in m["rows"]:
            store_id = next((row[k] for k in _STORE_ID_KEYS if k in row), None)
            candidates.append(
                {"table": m["table"], "matched_column": m["column"], "store_id": store_id, "row": row}
            )

    distinct_ids = {c["store_id"] for c in candidates if c["store_id"] is not None}
    ambiguous = len(distinct_ids) > 1
    note = None
    if candidates and not distinct_ids:
        note = (
            "Matches found but no obvious store_id/id column in the matched rows; "
            "inspect candidates[].row manually."
        )

    return {
        "name_or_domain": name_or_domain,
        "searched_columns": [f"{s}.{t}.{c}" for s, t, c in targets],
        "truncated": truncated,
        "ambiguous": ambiguous,
        "store_id": next(iter(distinct_ids)) if (distinct_ids and not ambiguous) else None,
        "candidates": candidates,
        "skipped": skipped,
        "note": note,
    }
