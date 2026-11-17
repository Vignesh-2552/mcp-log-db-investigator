from dataclasses import dataclass

import sqlglot
from sqlalchemy.exc import SQLAlchemyError
from sqlglot import exp

from opsinsight.core.errors import GuardrailError
from opsinsight.core.logging_config import get_logger
from opsinsight.core.redaction import is_sensitive_key, redact_text
from opsinsight.integrations.database.constants import (
    BLOCKED_NODES,
    BLOCKED_TABLES,
    DANGEROUS_FUNCTION_PREFIXES,
    DANGEROUS_FUNCTIONS,
    DIALECT,
    MAX_SQL_CHARS,
)

logger = get_logger("database.guardrail")


def sqlalchemy_error_detail(e: SQLAlchemyError) -> str:
    """Best-effort extraction of the underlying driver error message —
    `.orig` is the raw DBAPI exception (e.g. asyncpg's), which is usually
    more specific than SQLAlchemy's own wrapper message.

    Redacted: Postgres echoes offending values in errors (e.g. `invalid input
    syntax for type integer: "a@b.com"` from `SELECT email::int`), which
    would otherwise bypass row redaction."""
    orig = getattr(e, "orig", None)
    return redact_text(str(orig) if orig is not None else str(e))


@dataclass
class ValidatedQuery:
    sql: str
    tables: list[str]
    limit: int


def _table_name(table: exp.Table) -> str:
    """Reports the table as referenced in the query — schema-qualified if
    the query qualified it, unqualified otherwise. Doesn't force a schema
    (e.g. 'public'): the DB itself resolves unqualified names via its own
    search_path, and this server isn't scoped to any single schema."""
    if table.db:
        return f"{table.db}.{table.name}".lower()
    return table.name.lower()


def _function_name(node: exp.Func) -> str:
    # `.name` is the bare name for both `"pg_sleep"(...)` (quoted Identifier)
    # and `pg_catalog.pg_sleep(...)`; str(node.this) would keep the quotes.
    if isinstance(node, exp.Anonymous):
        return node.name.lower()
    return node.sql_name().lower()


def _is_dangerous_function(name: str) -> bool:
    return name in DANGEROUS_FUNCTIONS or name.startswith(DANGEROUS_FUNCTION_PREFIXES)


def _projection_of(node: exp.Expression) -> exp.Expression | None:
    """The select-list item `node` sits in, or None if it is outside every
    select list (WHERE, JOIN, ORDER BY, ...)."""
    current = node
    while current.parent is not None:
        if isinstance(current.parent, exp.Select) and current.arg_key == "expressions":
            return current
        current = current.parent
    return None


def _inside(node: exp.Expression, stop: exp.Expression, kinds: tuple[type, ...]) -> bool:
    current = node
    while current is not None and current is not stop:
        if isinstance(current, kinds):
            return True
        current = current.parent
    return False


def _check_projection_redaction(tree: exp.Expression) -> None:
    """Redaction masks values by *output column name*, so a sensitive column
    must not reach the result under a name that doesn't look sensitive:
    `password_hash AS x`, `upper(token)`, a UNION branch (positional names),
    a derived table/CTE with a column-alias list, or a whole-row value
    (`SELECT u FROM users u`, `row_to_json(u)`), whose fields can't be
    masked individually. Selecting the column under its own (or another
    sensitive-looking) name is fine: it comes back masked."""
    table_names = {t.alias_or_name.lower() for t in tree.find_all(exp.Table)}
    renames_columns = any(a.columns for a in tree.find_all(exp.TableAlias))
    has_set_operation = any(True for _ in tree.find_all(exp.SetOperation))

    # `t.*` / `*` is fine as a select-list item itself (columns keep their
    # names) or inside COUNT, but anywhere else in a projection it turns a
    # whole row into one value: row_to_json(t.*), json_agg(t.*), (t.*)::text.
    for star in tree.find_all(exp.Star):
        node = star.parent if isinstance(star.parent, exp.Column) else star
        projection = _projection_of(node)
        if projection is None or projection is node:
            continue
        if isinstance(projection, exp.Count) or _inside(node, projection, (exp.Count,)):
            continue
        raise GuardrailError(
            rule="whole_row_reference",
            message=f"Whole-row value {node.sql(dialect=DIALECT)!r} inside an expression is not allowed.",
            detail="Row values can't be PII-masked field by field. Select the columns you need "
            "(or `alias.*` on its own) instead.",
        )

    for column in tree.find_all(exp.Column):
        projection = _projection_of(column)
        if projection is None:
            continue
        name = column.name.lower()
        if not column.table and name in table_names:
            raise GuardrailError(
                rule="whole_row_reference",
                message=f"Whole-row reference to {column.name!r} is not allowed.",
                detail="Row values can't be PII-masked field by field. Select the columns you need "
                "(or `alias.*`) instead.",
            )
        if not is_sensitive_key(name) or _inside(column, projection, (exp.Count,)):
            continue
        if has_set_operation or renames_columns or not is_sensitive_key(projection.alias_or_name):
            raise GuardrailError(
                rule="sensitive_column_renamed",
                message=f"Sensitive column {column.name!r} would be returned under another name.",
                detail="Values are masked by column name. Select it under its own name (it comes back "
                "masked), use COUNT(...), filter on it in WHERE, or leave it out. It can't be "
                "aliased, wrapped in an expression, or projected through UNION or a column-alias list.",
            )


def clamp_and_inject_limit(
    tree: exp.Expression, max_rows: int, requested_limit: int | None
) -> tuple[exp.Expression, int]:
    """Injects LIMIT if absent, clamps it to `max_rows` otherwise.

    For a UNION/INTERSECT/EXCEPT root the LIMIT caps the combined result.
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
    if len(sql) > MAX_SQL_CHARS:
        raise GuardrailError(
            rule="query_too_long",
            message=f"SQL is {len(sql)} characters; the maximum is {MAX_SQL_CHARS}.",
        )
    try:
        # A trailing `; -- comment` parses as an extra Semicolon node; it isn't a statement.
        statements = [
            s for s in sqlglot.parse(sql, read=DIALECT) if s is not None and not isinstance(s, exp.Semicolon)
        ]
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

    # SetOperation covers UNION / INTERSECT / EXCEPT of SELECTs; its branches
    # are walked by the same blocked-node/function checks below.
    if not isinstance(tree, (exp.Select, exp.With, exp.SetOperation)):
        logger.warning("SQL validation failed (rule=root_not_select): Root node was %s", type(tree).__name__)
        raise GuardrailError(
            rule="root_not_select",
            message="Only SELECT, WITH ... SELECT and UNION/INTERSECT/EXCEPT of SELECTs are permitted.",
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
            if _is_dangerous_function(fname):
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
        if table.name.lower() in BLOCKED_TABLES:
            logger.warning("SQL validation failed (rule=blocked_table): %s", table.name)
            raise GuardrailError(
                rule="blocked_table",
                message=f"Blocked system table: {table.name}.",
                detail="It exposes credentials, auth config or other sessions' unredacted query text.",
                allowed=None,
            )
        referenced_tables.append(_table_name(table))

    _check_projection_redaction(tree)

    tree, limit = clamp_and_inject_limit(tree, max_rows, requested_limit)

    logger.debug("SQL validation successful. Referenced tables: %s, injected limit: %d", referenced_tables, limit)
    return ValidatedQuery(sql=tree.sql(dialect=DIALECT), tables=referenced_tables, limit=limit)


def build_explain_sql(validated_sql: str) -> str:
    return f"EXPLAIN (FORMAT JSON) {validated_sql}"
