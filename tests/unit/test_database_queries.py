from integrations.database.queries import build_sample_rows_sql


def test_sample_rows_quotes_schema_and_table_identifiers():
    assert build_sample_rows_sql("reporting", "order") == (
        'SELECT * FROM "reporting"."order" LIMIT :limit'
    )


def test_sample_rows_escapes_crafted_identifier_as_identifier():
    table = 'users"; SELECT pg_read_file(\'/etc/passwd\'); --'

    sql = build_sample_rows_sql("public", table)

    assert sql == (
        'SELECT * FROM "public".'
        '"users""; SELECT pg_read_file(\'/etc/passwd\'); --" LIMIT :limit'
    )


def test_sample_rows_quotes_unqualified_table():
    assert build_sample_rows_sql(None, "User Accounts") == (
        'SELECT * FROM "User Accounts" LIMIT :limit'
    )
