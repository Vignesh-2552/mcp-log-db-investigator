from typing import Any

from sqlalchemy import inspect, text

from investigation_server.cache import ttl_cache
from investigation_server.config import Settings, get_settings
from investigation_server.database.engine import get_engine
from investigation_server.errors import GuardrailError
from investigation_server.logging_config import get_logger
from investigation_server.redaction import redact_rows

logger = get_logger("database.introspect")


def _split_table(table: str) -> tuple[str, str]:
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "public", table
    return schema, name


def _sync_describe_table(sync_conn, name: str, schema: str) -> dict[str, Any]:
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
    qualified = f"{schema}.{name}".lower()
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
                "references_table": f"{fk.get('referred_schema') or schema}.{fk['referred_table']}",
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
    qualified = f"{schema}.{name}".lower()
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
IDENTIFIER_SEARCH_MAP: dict[str, list[tuple[str, str]]] = {
    "order_id": [("public.orders", "id"), ("public.payments", "order_id")],
    "user_id": [("public.orders", "user_id"), ("public.users", "id")],
    "payment_id": [("public.payments", "id")],
    "request_id": [("public.orders", "request_id")],
}


async def search_by_identifier(
    identifier: str, id_type: str, settings: Settings | None = None
) -> dict[str, Any]:
    if id_type not in IDENTIFIER_SEARCH_MAP:
        raise GuardrailError(
            rule="unknown_id_type",
            message=f"Unknown id_type: {id_type}.",
            allowed=sorted(IDENTIFIER_SEARCH_MAP.keys()),
        )
    settings = settings or get_settings()
    engine = get_engine(settings)
    matches: list[dict[str, Any]] = []
    async with engine.connect() as conn:
        for qualified, column in IDENTIFIER_SEARCH_MAP[id_type]:
            result = await conn.execute(
                text(f"SELECT * FROM {qualified} WHERE {column} = :identifier LIMIT :limit"),
                {"identifier": identifier, "limit": settings.db_max_rows},
            )
            columns = list(result.keys())
            rows = [dict(zip(columns, row)) for row in result.fetchall()]
            if rows:
                matches.append(
                    {
                        "table": qualified,
                        "column": column,
                        "rows": redact_rows(rows, settings),
                        "row_count": len(rows),
                    }
                )
    return {"identifier": identifier, "id_type": id_type, "matches": matches}
