from dataclasses import dataclass

import sqlglot
from sqlglot import exp

from core.errors import GuardrailError
from core.logging_config import get_logger

logger = get_logger("database.guardrail")

DIALECT = "postgres"

# Reject any of these node types anywhere in the tree.
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

# Reject calls to these functions anywhere in the tree.
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
    """Truncates str/bytes cells over max_bytes."""
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
    max_rows: int,
    requested_limit: int | None = None,
) -> ValidatedQuery:
    """The DB security boundary. Raises GuardrailError with
    a specific machine-readable `rule` on the first violation found, so the
    client model gets a self-correctable error rather than a bare denial.
    """
    logger.debug("Validating SQL query (max_rows=%d, requested_limit=%s)", max_rows, requested_limit)
    try:
        statements = [s for s in sqlglot.parse(sql, read=DIALECT) if s is not None]
    except Exception as e:
        logger.warning("SQL validation failed (rule=parse_error): %s", e)
        raise GuardrailError(rule="parse_error", message="Could not parse SQL.", detail=str(e)) from e

    if len(statements) == 0:
        logger.warning("SQL validation failed: No statement found")
        raise GuardrailError(rule="parse_error", message="No SQL statement found.")
    if len(statements) > 1:
        logger.warning("SQL validation failed (rule=multiple_statements): Found %d statements", len(statements))
        raise GuardrailError(
            rule="multiple_statements",
            message="Exactly one SQL statement is allowed per call.",
            detail=f"Found {len(statements)} statements; stacked/batched statements are not permitted.",
        )

    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.With)):
        logger.warning("SQL validation failed (rule=root_not_select): Root node was %s", type(tree).__name__)
        raise GuardrailError(
            rule="root_not_select",
            message="Only SELECT and WITH ... SELECT statements are permitted.",
            detail=f"Root node was {type(tree).__name__}.",
        )

    for node in tree.walk():
        if isinstance(node, BLOCKED_NODES):
            logger.warning("SQL validation failed (rule=blocked_operation): Node %s", type(node).__name__)
            raise GuardrailError(
                rule="blocked_operation",
                message=f"Blocked operation: {type(node).__name__}.",
                detail=node.sql(dialect=DIALECT),
            )
        if isinstance(node, exp.Func):
            fname = _function_name(node)
            if fname in DANGEROUS_FUNCTIONS:
                logger.warning("SQL validation failed (rule=blocked_function): Function %s()", fname)
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
        referenced_tables.append(_table_name(table))

    tree, limit = clamp_and_inject_limit(tree, max_rows, requested_limit)

    logger.debug("SQL validation successful. Referenced tables: %s, injected limit: %d", referenced_tables, limit)
    return ValidatedQuery(sql=tree.sql(dialect=DIALECT), tables=referenced_tables, limit=limit)


def build_explain_sql(validated_sql: str) -> str:
    return f"EXPLAIN (FORMAT JSON) {validated_sql}"
