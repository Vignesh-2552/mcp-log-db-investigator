import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_env(settings_override, tmp_path):
    # Keep isolated Settings/DB engine caches per test.
    await settings_override()
    return tmp_path


async def test_db_list_tables_returns_tables(db_env):
    from tools.database_tools import db_list_tables

    result = await db_list_tables(schema="private")
    assert result["ok"] is True
    assert len(result["data"]["tables"]) > 0


async def test_db_describe_table_returns_columns(db_env):
    from tools.database_tools import db_describe_table

    result = await db_describe_table("private.orders")
    assert result["ok"] is True
    col_names = {c["name"] for c in result["data"]["columns"]}
    assert {"id", "customer_id", "status", "created_at"} <= col_names
    assert result["data"]["primary_key"] == ["id"]


async def test_db_run_query_returns_a_real_order(db_env):
    from tools.database_tools import db_run_query

    result = await db_run_query(
        "SELECT id, order_number, customer_id, status FROM private.orders ORDER BY created_at DESC LIMIT 1"
    )
    assert result["ok"] is True
    assert result["data"]["row_count"] == 1
    assert "executed_sql" in result["data"]


async def test_db_run_query_rejects_write_end_to_end(db_env):
    from tools.database_tools import db_run_query

    result = await db_run_query("DELETE FROM private.orders WHERE id = '00000000-0000-0000-0000-000000000000'")
    assert result["ok"] is False
    assert result["error"]["rule"] in ("blocked_operation", "root_not_select")


async def test_db_search_by_identifier_finds_order_and_payment(db_env):
    from tools.database_tools import db_run_query, db_search_by_identifier

    seed = await db_run_query(
        "SELECT order_id FROM private.order_payment ORDER BY created_at DESC LIMIT 1"
    )
    assert seed["ok"] is True and seed["data"]["row_count"] == 1
    order_id = str(seed["data"]["rows"][0]["order_id"])

    result = await db_search_by_identifier(order_id, "order_id")
    assert result["ok"] is True
    tables_hit = {m["table"] for m in result["data"]["matches"]}
    assert tables_hit == {"private.orders", "private.order_payment"}


async def test_db_sample_rows_masks_pii(db_env):
    from tools.database_tools import db_sample_rows

    result = await db_sample_rows("private.customer", limit=5)
    assert result["ok"] is True
    for row in result["data"]["rows"]:
        assert row["email"] == "[REDACTED]"
