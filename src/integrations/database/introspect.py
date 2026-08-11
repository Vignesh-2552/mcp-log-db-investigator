import asyncio
from typing import Any

from sqlalchemy import inspect, text

from core.cache import ttl_cache
from core.config import Settings, get_settings
from core.errors import GuardrailError
from core.logging_config import get_logger
from core.redaction import redact_rows
from integrations.database.engine import get_engine

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


async def search_by_identifier(
    identifier: str, id_type: str, settings: Settings | None = None
) -> dict[str, Any]:
    settings = settings or get_settings()

    column_matches = await _find_tables_with_column(id_type)
    entity_matches = await _find_entity_tables(tuple(_entity_name_candidates(id_type)))

    # Entity matches first — e.g. for id_type="order_id" the "orders" table
    # itself (the actual entity) matters more than the dozens of unrelated
    # tables that merely have a foreign-key column named "order_id", and
    # must not get pushed out by the _MAX_SEARCH_TARGETS cap.
    targets: list[tuple[str, str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    for schema, table, pk_column in entity_matches:
        key = (schema, table, pk_column)
        if key not in seen:
            seen.add(key)
            targets.append(key)
    for schema, table in column_matches:
        key = (schema, table, id_type)
        if key not in seen:
            seen.add(key)
            targets.append(key)

    if not targets:
        raise GuardrailError(
            rule="no_matching_tables",
            message=f"No column named '{id_type}' and no table matching that entity name were found.",
            detail="Try db_list_tables or db_describe_table to find the right column name.",
        )

    truncated = len(targets) > _MAX_SEARCH_TARGETS
    targets = targets[:_MAX_SEARCH_TARGETS]

    engine = get_engine(settings)

    async def _search_one(schema: str, table: str, column: str) -> dict[str, Any] | None:
        qualified = f"{schema}.{table}"
        async with engine.connect() as conn:
            result = await conn.execute(
                text(f'SELECT * FROM "{schema}"."{table}" WHERE "{column}" = :identifier LIMIT :limit'),
                {"identifier": identifier, "limit": settings.db_max_rows},
            )
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
        if not rows:
            return None
        return {
            "table": qualified,
            "column": column,
            "rows": redact_rows(rows, settings),
            "row_count": len(rows),
        }

    # Each target gets its own pooled connection, run concurrently — with
    # up to _MAX_SEARCH_TARGETS tables to check, doing this serially would
    # scale linearly with per-query network latency (25 tables x ~1s+ each
    # over a real network is a genuinely bad wait).
    results = await asyncio.gather(*(_search_one(s, t, c) for s, t, c in targets))
    matches = [m for m in results if m is not None]
    return {
        "identifier": identifier,
        "id_type": id_type,
        "searched_tables": [f"{s}.{t}" for s, t, _ in targets],
        "truncated": truncated,
        "matches": matches,
    }
