import pytest

from investigation_server.db.guardrail import truncate_cell, validate_sql
from investigation_server.errors import GuardrailError

ALLOWLIST = frozenset({"public.orders", "public.users", "public.payments", "public.audit_events"})


def test_valid_select_passes():
    result = validate_sql("SELECT id, status FROM public.orders WHERE id = 1", ALLOWLIST, max_rows=500)
    assert result.tables == ["public.orders"]
    assert "LIMIT 500" in result.sql.upper()


def test_unqualified_table_defaults_to_public_schema():
    result = validate_sql("SELECT * FROM orders", ALLOWLIST, max_rows=500)
    assert result.tables == ["public.orders"]


def test_stacked_statements_rejected():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELECT 1; DROP TABLE public.orders", ALLOWLIST, max_rows=500)
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
        validate_sql(sql, ALLOWLIST, max_rows=500)
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
        validate_sql(sql, ALLOWLIST, max_rows=500)
    assert exc.value.rule == "blocked_function"


def test_table_not_in_allowlist_rejected_with_allowed_list():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELECT * FROM public.secret_table", ALLOWLIST, max_rows=500)
    assert exc.value.rule == "table_not_allowed"
    assert exc.value.allowed == sorted(ALLOWLIST)


def test_limit_clamped_to_max_rows():
    result = validate_sql("SELECT * FROM public.orders LIMIT 10000", ALLOWLIST, max_rows=500)
    assert result.limit == 500
    assert "LIMIT 500" in result.sql.upper()


def test_limit_injected_when_missing():
    result = validate_sql("SELECT * FROM public.orders", ALLOWLIST, max_rows=500)
    assert result.limit == 500
    assert "LIMIT" in result.sql.upper()


def test_requested_limit_respected_when_under_max():
    result = validate_sql("SELECT * FROM public.orders", ALLOWLIST, max_rows=500, requested_limit=25)
    assert result.limit == 25


def test_requested_limit_cannot_exceed_max_rows():
    result = validate_sql("SELECT * FROM public.orders", ALLOWLIST, max_rows=500, requested_limit=999999)
    assert result.limit == 500


def test_cte_limit_applies_to_outer_select_only():
    sql = (
        "WITH recent AS (SELECT * FROM public.orders) "
        "SELECT * FROM recent JOIN public.users u ON u.id = recent.user_id"
    )
    result = validate_sql(sql, ALLOWLIST, max_rows=500, requested_limit=50)
    assert result.limit == 50
    # the CTE alias itself must not be treated as a real table requiring allowlisting
    assert set(result.tables) == {"public.orders", "public.users"}
    # exactly one LIMIT clause, attached to the outer select
    assert result.sql.upper().count("LIMIT") == 1


def test_parse_error_rejected():
    with pytest.raises(GuardrailError) as exc:
        validate_sql("SELEKT * FRM orders", ALLOWLIST, max_rows=500)
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
