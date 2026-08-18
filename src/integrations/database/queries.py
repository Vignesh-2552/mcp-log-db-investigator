LIST_TABLES_SQL = """\
SELECT n.nspname AS schema, c.relname AS name,
       c.reltuples::bigint AS estimate, obj_description(c.oid) AS comment
FROM pg_class c
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND (CAST(:schema AS text) IS NULL OR n.nspname = CAST(:schema AS text))
ORDER BY n.nspname, c.relname
"""

COLUMN_SEARCH_SQL = """\
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

ENTITY_TABLE_SQL = """\
SELECT n.nspname AS schema, c.relname AS table_name, a.attname AS pk_column
FROM pg_index i
JOIN pg_class c ON c.oid = i.indrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
JOIN pg_attribute a ON a.attrelid = c.oid AND a.attnum = ANY(i.indkey)
WHERE i.indisprimary
  AND c.relkind IN ('r', 'p')
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND c.relname = ANY(CAST(:table_names AS text[]))
  AND cardinality(i.indkey) = 1
"""

STORE_COLUMN_SEARCH_SQL = """\
SELECT n.nspname AS schema, c.relname AS table_name, a.attname AS column_name
FROM pg_attribute a
JOIN pg_class c ON c.oid = a.attrelid
JOIN pg_namespace n ON n.oid = c.relnamespace
WHERE c.relkind IN ('r', 'p')
  AND a.attnum > 0 AND NOT a.attisdropped
  AND n.nspname NOT IN ('pg_catalog', 'information_schema', 'pg_toast')
  AND a.attname = ANY(CAST(:column_names AS text[]))
ORDER BY n.nspname, c.relname
"""


def quote_ident(name: str) -> str:
    """Double-quotes a SQL identifier, escaping embedded quotes. Postgres
    identifiers can legally contain characters (including '"') that would
    otherwise break out of naive f-string interpolation when building
    catalog-discovered table/column references into SQL text."""
    return '"' + name.replace('"', '""') + '"'


def build_sample_rows_sql(qualified_table: str) -> str:
    return f"SELECT * FROM {qualified_table} LIMIT :limit"


def build_identifier_lookup_sql(schema: str, table: str, column: str) -> str:
    return (
        f"SELECT * FROM {quote_ident(schema)}.{quote_ident(table)} "
        f"WHERE {quote_ident(column)} = :identifier"
    )


def build_store_lookup_sql(schema: str, table: str, column: str) -> str:
    return (
        f"SELECT * FROM {quote_ident(schema)}.{quote_ident(table)} "
        f"WHERE {quote_ident(column)} ILIKE :pattern"
    )
