import pytest

from opsinsight.core.errors import GuardrailError
from opsinsight.core.limits import truncate_cell
from opsinsight.integrations.database.guardrail import validate_sql


def test_valid_select_passes():
    result = validate_sql("SELECT id, status FROM public.orders WHERE id = 1", max_rows=500)
    assert result.tables == ["public.orders"]
    assert "LIMIT 500" in result.sql.upper()


def test_unqualified_table_stays_unqualified():
    """No forced 'public' schema — unqualified names resolve via the DB's
    own search_path, since this server isn't scoped to a single schema."""
    result = validate_sql("SELECT * FROM orders", max_rows=500)
    assert result.tables == ["orders"]


def test_qualified_table_with_non_public_schema_preserved():
    result = validate_sql("SELECT * FROM private.orders", max_rows=500)
    assert result.tables == ["private.orders"]


def test_stacked_statements_rejected():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELECT 1; DROP TABLE public.orders", max_rows=500)
    assert exc.value.rule == "multiple_statements"


@pytest.mark.parametrize(
    "sql",
    [
        "INSERT INTO public.orders (id) VALUES (1)",
        "UPDATE public.orders SET status = 'X'",
        "DELETE FROM public.orders",
        "CREATE TABLE public.evil (id int)",
        "DROP TABLE public.orders",
        "ALTER TABLE public.orders ADD COLUMN x int",
        "GRANT SELECT ON public.orders TO someone",
        "TRUNCATE TABLE public.orders",
    ],
)
def test_blocked_ddl_dml_rejected(sql):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(sql, max_rows=500)
    assert exc.value.rule in ("blocked_operation", "root_not_select")


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT pg_sleep(5)",
        "SELECT dblink('conn', 'select 1')",
        "SELECT pg_read_file('/etc/passwd')",
    ],
)
def test_dangerous_function_calls_rejected(sql):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(sql, max_rows=500)
    assert exc.value.rule == "blocked_function"


@pytest.mark.parametrize(
    "sql",
    [
        # Each of these passed the guardrail before the 2026-10-28 security audit.
        "SELECT dblink_exec('dbname=app', 'DROP TABLE orders')",
        "SELECT * FROM dblink_send_query('c', 'DELETE FROM orders')",
        "SELECT * FROM public.dblink('dbname=app', 'SELECT 1') AS t(a int)",
        "SELECT query_to_xml('SELECT pg_read_file(''/etc/passwd'')', true, false, '')",
        "SELECT pg_sleep_for('10 seconds')",
        "SELECT pg_sleep_until(now() + interval '1 hour')",
        'SELECT "pg_sleep"(5)',
        "SELECT pg_catalog.pg_sleep(5)",
        "SELECT pg_advisory_lock(1)",
        "SELECT pg_try_advisory_lock(1)",
        "SELECT set_config('statement_timeout', '0', false)",
        "SELECT lo_get(1234)",
        "SELECT pg_ls_logdir()",
        "SELECT pg_stat_file('postgresql.conf')",
        "SELECT pg_reload_conf()",
        "SELECT nextval('orders_id_seq')",
        "SELECT database_to_xml(true, false, '')",
        "SELECT 1 UNION SELECT dblink_exec('x', 'y')",
    ],
)
def test_guardrail_bypass_functions_rejected(sql):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(sql, max_rows=500)
    assert exc.value.rule == "blocked_function"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT * INTO public.evil FROM public.orders",
        "SELECT * FROM public.orders FOR UPDATE",
        "SELECT * FROM public.orders FOR SHARE",
    ],
)
def test_select_into_and_row_locks_rejected(sql):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(sql, max_rows=500)
    assert exc.value.rule == "blocked_operation"


def test_ordinary_functions_still_allowed():
    sql = "SELECT lower(status), count(*), current_setting('TimeZone') FROM public.orders GROUP BY 1"
    assert validate_sql(sql, max_rows=500).tables == ["public.orders"]


def test_oversized_sql_rejected():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELECT 1 " + " " * 60_000, max_rows=500)
    assert exc.value.rule == "query_too_long"


def test_limit_clamped_to_max_rows():
    result = validate_sql("SELECT * FROM public.orders LIMIT 10000", max_rows=500)
    assert result.limit == 500
    assert "LIMIT 500" in result.sql.upper()


def test_limit_injected_when_missing():
    result = validate_sql("SELECT * FROM public.orders", max_rows=500)
    assert result.limit == 500
    assert "LIMIT" in result.sql.upper()


def test_requested_limit_respected_when_under_max():
    result = validate_sql("SELECT * FROM public.orders", max_rows=500, requested_limit=25)
    assert result.limit == 25


def test_requested_limit_cannot_exceed_max_rows():
    result = validate_sql("SELECT * FROM public.orders", max_rows=500, requested_limit=999999)
    assert result.limit == 500


def test_cte_limit_applies_to_outer_select_only():
    sql = (
        "WITH recent AS (SELECT * FROM public.orders) "
        "SELECT * FROM recent JOIN public.users u ON u.id = recent.user_id"
    )
    result = validate_sql(sql, max_rows=500, requested_limit=50)
    assert result.limit == 50
    # the CTE alias itself must not be treated as a real table
    assert set(result.tables) == {"public.orders", "public.users"}
    # exactly one LIMIT clause, attached to the outer select
    assert result.sql.upper().count("LIMIT") == 1


@pytest.mark.parametrize("op", ["UNION", "UNION ALL", "INTERSECT", "EXCEPT"])
def test_set_operations_allowed_with_limit_on_combined_result(op):
    sql = f"SELECT id FROM public.orders {op} SELECT order_id FROM public.payments"
    result = validate_sql(sql, max_rows=500, requested_limit=20)
    assert set(result.tables) == {"public.orders", "public.payments"}
    assert result.sql.upper().endswith("LIMIT 20")
    assert result.sql.upper().count("LIMIT") == 1


def test_set_operation_branch_still_checked_for_blocked_functions():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELECT 1 UNION SELECT pg_sleep(5)", max_rows=500)
    assert exc.value.rule == "blocked_function"


def test_parse_error_rejected():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELEKT * FRM orders", max_rows=500)
    assert exc.value.rule in ("parse_error", "root_not_select", "table_not_allowed")


def test_truncate_cell_under_limit_unchanged():
    assert truncate_cell("short", max_bytes=4096) == "short"


def test_truncate_cell_over_limit_truncated():
    value = "x" * 5000
    result = truncate_cell(value, max_bytes=4096)
    assert result != value
    assert result.startswith("x" * 100)
    assert "truncated 5000b" in result


def test_truncate_cell_non_string_passthrough():
    assert truncate_cell(42, max_bytes=4096) == 42
    assert truncate_cell(None, max_bytes=4096) is None


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT password_hash AS x FROM users",
        "SELECT upper(token) FROM sessions",
        "SELECT name FROM a UNION SELECT password_hash FROM users",
        "SELECT x FROM (SELECT api_key FROM integrations) AS s(x)",
        "WITH c(x) AS (SELECT client_secret FROM apps) SELECT x FROM c",
    ],
)
def test_sensitive_column_cannot_be_renamed(sql):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(sql, max_rows=500)
    assert exc.value.rule == "sensitive_column_renamed"


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id, password_hash FROM users",
        "SELECT token AS session_token FROM sessions",
        "SELECT count(token) AS n FROM sessions",
        "SELECT id FROM sessions WHERE token IS NOT NULL ORDER BY id",
        "SELECT u.* FROM users u",
        "SELECT * FROM users",
        "SELECT u.*, o.id FROM users u JOIN orders o ON o.user_id = u.id",
        "SELECT count(*) FROM users",
        "SELECT count(u.*) FROM users u",
        "SELECT * FROM (SELECT * FROM users) s",
    ],
)
def test_sensitive_column_under_a_masked_name_is_allowed(sql):
    validate_sql(sql, max_rows=500)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT u FROM users u",
        "SELECT row_to_json(u) FROM users u",
        "SELECT row_to_json(users.*) FROM users",
        "SELECT row_to_json(t.*) FROM users AS t",
        "SELECT json_agg(users.*) FROM users",
        "SELECT jsonb_agg(t.*) FROM users t",
        "SELECT array_agg(t.*) FROM users t",
        "SELECT COALESCE(row_to_json(users.*), '{}') FROM users",
        "SELECT (u.*)::text FROM users u",
        "SELECT id FROM (SELECT row_to_json(u.*) AS r FROM users u) s",
    ],
)
def test_whole_row_reference_is_rejected(sql):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(sql, max_rows=500)
    assert exc.value.rule == "whole_row_reference"


@pytest.mark.parametrize("table", ["pg_shadow", "pg_catalog.pg_authid", "pg_stat_activity", "pg_hba_file_rules"])
def test_sensitive_system_tables_are_blocked(table):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(f"SELECT * FROM {table}", max_rows=500)
    assert exc.value.rule == "blocked_table"


@pytest.mark.parametrize("fn", ["pg_stat_reset()", "pg_stat_statements_reset()"])
def test_stats_reset_functions_are_blocked(fn):
    with pytest.raises(GuardrailError) as exc:
        validate_sql(f"SELECT {fn}", max_rows=500)
    assert exc.value.rule == "blocked_function"


@pytest.mark.parametrize("sql", ["SELECT 1; -- trailing note", "SELECT 1; /* note */"])
def test_trailing_comment_after_semicolon_is_one_statement(sql):
    assert validate_sql(sql, max_rows=500).sql.upper().startswith("SELECT 1")
