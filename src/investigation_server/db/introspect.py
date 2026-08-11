from typing import Any

from sqlalchemy import inspect, text

from investigation_server.cache import ttl_cache
from investigation_server.config import Settings, get_settings
from investigation_server.db.engine import get_engine
from investigation_server.errors import GuardrailError
from investigation_server.redaction import redact_rows


def _split_table(table: str) -> tuple[str, str]:
    if "." in table:
        schema, name = table.split(".", 1)
    else:
        schema, name = "public", table
    return schema, name


def _check_allowed(table: str, allowlist: frozenset[str]) -> str:
    schema, name = _split_table(table)
    qualified = f"{schema}.{name}".lower()
    if qualified not in allowlist:
        raise GuardrailError(
            rule="table_not_allowed",
            message=f"Table not allowed: {qualified}.",
            allowed=sorted(allowlist),
        )
    return qualified


@ttl_cache(maxsize=8, ttl_seconds=600)
def _list_tables_cached(schema: str | None, allowlist: frozenset[str]) -> list[dict[str, Any]]:
    settings = get_settings()
    engine = get_engine(settings)
    inspector = inspect(engine)
    schemas = [schema] if schema else sorted({_split_table(t)[0] for t in allowlist})
    results: list[dict[str, Any]] = []
    with engine.connect() as conn:
        for sch in schemas:
            for name in inspector.get_table_names(schema=sch):
                qualified = f"{sch}.{name}".lower()
                if qualified not in allowlist:
                    continue
                estimate = conn.execute(
                    text(
                        "SELECT reltuples::bigint AS estimate FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relname = :name"
                    ),
                    {"schema": sch, "name": name},
                ).scalar()
                comment = conn.execute(
                    text(
                        "SELECT obj_description(c.oid) FROM pg_class c "
                        "JOIN pg_namespace n ON n.oid = c.relnamespace "
                        "WHERE n.nspname = :schema AND c.relname = :name"
                    ),
                    {"schema": sch, "name": name},
                ).scalar()
                results.append(
                    {
                        "table": qualified,
                        "row_estimate": int(estimate) if estimate is not None else None,
                        "comment": comment,
                    }
                )
    return results


def list_tables(schema: str | None, allowlist: frozenset[str]) -> list[dict[str, Any]]:
    """Cached ~10min per doc §4.1."""
    return _list_tables_cached(schema, allowlist)


def describe_table(table: str, allowlist: frozenset[str]) -> dict[str, Any]:
    qualified = _check_allowed(table, allowlist)
    schema, name = _split_table(qualified)
    engine = get_engine()
    inspector = inspect(engine)
    columns = inspector.get_columns(name, schema=schema)
    pk = inspector.get_pk_constraint(name, schema=schema)
    fks = inspector.get_foreign_keys(name, schema=schema)
    indexes = inspector.get_indexes(name, schema=schema)
    return {
        "table": qualified,
        "columns": [
            {
                "name": c["name"],
                "type": str(c["type"]),
                "nullable": c["nullable"],
                "default": str(c["default"]) if c.get("default") is not None else None,
            }
            for c in columns
        ],
        "primary_key": pk.get("constrained_columns", []),
        "foreign_keys": [
            {
                "columns": fk["constrained_columns"],
                "references_table": f"{fk.get('referred_schema') or schema}.{fk['referred_table']}",
                "references_columns": fk["referred_columns"],
            }
            for fk in fks
        ],
        "indexes": [
            {"name": idx["name"], "columns": idx["column_names"], "unique": idx["unique"]}
            for idx in indexes
        ],
    }


def sample_rows(table: str, limit: int, allowlist: frozenset[str], settings: Settings | None = None) -> dict[str, Any]:
    qualified = _check_allowed(table, allowlist)
    settings = settings or get_settings()
    clamped_limit = max(1, min(limit, settings.db_max_rows))
    engine = get_engine(settings)
    with engine.connect() as conn:
        result = conn.execute(text(f"SELECT * FROM {qualified} LIMIT :limit"), {"limit": clamped_limit})
        columns = list(result.keys())
        rows = [dict(zip(columns, row)) for row in result.fetchall()]
    rows = redact_rows(rows, settings)
    return {"table": qualified, "columns": columns, "rows": rows, "row_count": len(rows)}


# design doc §4.1 db_search_by_identifier — the "find order X" convenience flow.
IDENTIFIER_SEARCH_MAP: dict[str, list[tuple[str, str]]] = {
    "order_id": [("public.orders", "id"), ("public.payments", "order_id")],
    "user_id": [("public.orders", "user_id"), ("public.users", "id")],
    "payment_id": [("public.payments", "id")],
    "request_id": [("public.orders", "request_id")],
}


def search_by_identifier(
    identifier: str, id_type: str, allowlist: frozenset[str], settings: Settings | None = None
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
    with engine.connect() as conn:
        for qualified, column in IDENTIFIER_SEARCH_MAP[id_type]:
            if qualified not in allowlist:
                continue
            result = conn.execute(
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
