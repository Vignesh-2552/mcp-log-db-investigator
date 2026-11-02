from opsinsight.integrations.database.queries import build_sample_rows_sql


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


def test_identifier_lookup_orders_newest_first_and_passes_the_guardrail():
    from opsinsight.integrations.database.guardrail import validate_sql
    from opsinsight.integrations.database.queries import build_identifier_lookup_sql

    sql = build_identifier_lookup_sql("public", "orders", "order_id", "created_at")

    assert sql == (
        'SELECT * FROM "public"."orders" WHERE "order_id" = :identifier '
        'ORDER BY "created_at" DESC NULLS LAST'
    )
    assert validate_sql(sql, max_rows=50).limit == 50
    assert "ORDER BY" not in build_identifier_lookup_sql("public", "orders", "order_id")


def test_pick_time_column_prefers_known_names_then_any_at_column():
    from opsinsight.integrations.database.introspect import pick_time_column

    assert pick_time_column(["updated_at", "created_at"]) == "created_at"
    assert pick_time_column(["birth_date", "shipped_at"]) == "shipped_at"
    assert pick_time_column(["birth_date"]) is None
