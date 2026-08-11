from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from investigation_server.errors import GuardrailError

DIALECT = "postgres"

# design doc §6.1 — reject any of these node types anywhere in the tree.
BLOCKED_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Merge,
    exp.Create,
    exp.Drop,
    exp.Alter,
    exp.Grant,
    exp.TruncateTable,
    exp.Copy,
    exp.Command,
)

# design doc §6.1 — reject calls to these functions anywhere in the tree.
DANGEROUS_FUNCTIONS = {
    "pg_read_file",
    "pg_read_binary_file",
    "pg_ls_dir",
    "pg_sleep",
    "dblink",
    "dblink_connect",
    "lo_import",
    "lo_export",
    "pg_terminate_backend",
    "pg_cancel_backend",
}


@dataclass
class ValidatedQuery:
    sql: str
    tables: list[str]
    limit: int


def _table_name(table: exp.Table) -> str:
    return f"{table.db or 'public'}.{table.name}".lower()


def _function_name(node: exp.Func) -> str:
    if isinstance(node, exp.Anonymous):
        return str(node.this).lower()
    return node.sql_name().lower()


def clamp_and_inject_limit(
    tree: exp.Expression, max_rows: int, requested_limit: int | None
) -> tuple[exp.Expression, int]:
    """Injects LIMIT if absent, clamps it to `max_rows` otherwise.

    Applies to the outer SELECT of a WITH...SELECT tree only — sqlglot
    represents the outer select and its CTEs' select bodies as separate
    nodes, so setting `tree`'s own `limit` arg never touches CTE internals.
    """
    target = min(requested_limit, max_rows) if requested_limit is not None else max_rows

    existing = tree.args.get("limit")
    if existing is not None:
        try:
            existing_value = int(existing.expression.this)
        except (AttributeError, TypeError, ValueError):
            existing_value = max_rows
        target = min(target, existing_value)

    target = max(target, 1)
    tree.set("limit", exp.Limit(expression=exp.Literal.number(target)))
    return tree, target


def truncate_cell(value: object, max_bytes: int = 4096) -> object:
    """Truncates str/bytes cells over max_bytes, per doc §6.1 result limits."""
    if isinstance(value, str):
        data = value.encode("utf-8")
        if len(data) > max_bytes:
            return data[:max_bytes].decode("utf-8", errors="ignore") + f"...[truncated {len(data)}b]"
        return value
    if isinstance(value, bytes):
        if len(value) > max_bytes:
            return value[:max_bytes] + f"...[truncated {len(value)}b]".encode()
        return value
    return value


def validate_sql(
    sql: str,
    allowlist: frozenset[str],
    max_rows: int,
    requested_limit: int | None = None,
) -> ValidatedQuery:
    """The DB security boundary (design doc §6.1). Raises GuardrailError with
    a specific machine-readable `rule` on the first violation found, so the
    client model gets a self-correctable error rather than a bare denial.
    """
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except Exception as e:
        raise GuardrailError(rule="parse_error", message="Could not parse SQL.", detail=str(e)) from e

    if len(statements) == 0:
        raise GuardrailError(rule="parse_error", message="No SQL statement found.")
    if len(statements) > 1:
        raise GuardrailError(
            rule="multiple_statements",
            message="Exactly one SQL statement is allowed per call.",
            detail=f"Found {len(statements)} statements; stacked/batched statements are not permitted.",
        )

    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.With)):
        raise GuardrailError(
            rule="root_not_select",
            message="Only SELECT and WITH ... SELECT statements are permitted.",
            detail=f"Root node was {type(tree).__name__}.",
        )

    for node in tree.walk():
        if isinstance(node, BLOCKED_NODES):
            raise GuardrailError(
                rule="blocked_operation",
                message=f"Blocked operation: {type(node).__name__}.",
                detail=node.sql(dialect=DIALECT),
            )
        if isinstance(node, exp.Func):
            fname = _function_name(node)
            if fname in DANGEROUS_FUNCTIONS:
                raise GuardrailError(
                    rule="blocked_function",
                    message=f"Blocked function call: {fname}().",
                    detail=node.sql(dialect=DIALECT),
                )

    cte_aliases = {cte.alias.lower() for cte in getattr(tree, "ctes", [])}
    referenced_tables: list[str] = []
    for table in tree.find_all(exp.Table):
        if not table.db and table.name.lower() in cte_aliases:
            continue
        name = _table_name(table)
        if name not in allowlist:
            raise GuardrailError(
                rule="table_not_allowed",
                message=f"Table not allowed: {name}.",
                allowed=sorted(allowlist),
            )
        referenced_tables.append(name)

    tree, limit = clamp_and_inject_limit(tree, max_rows, requested_limit)

    return ValidatedQuery(sql=tree.sql(dialect=DIALECT), tables=referenced_tables, limit=limit)


def build_explain_sql(validated_sql: str) -> str:
    return f"EXPLAIN (FORMAT JSON) {validated_sql}"
