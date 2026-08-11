import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
async def db_env(settings_override, tmp_path):
    # Keep isolated Settings/DB engine caches per test.
    await settings_override()
    return tmp_path


async def test_db_list_tables_returns_tables(db_env):
    from investigation_server.database.tools import db_list_tables

    result = await db_list_tables()
    assert result["ok"] is True
    assert len(result["data"]["tables"]) > 0


async def test_db_describe_table_returns_columns(db_env):
    from investigation_server.database.tools import db_describe_table

    result = await db_describe_table("public.orders")
    assert result["ok"] is True
    col_names = {c["name"] for c in result["data"]["columns"]}
    assert {"id", "user_id", "status", "error_code", "created_at"} <= col_names
    assert result["data"]["primary_key"] == ["id"]


async def test_db_run_query_returns_seeded_failed_order(db_env):
    from investigation_server.database.tools import db_run_query

    result = await db_run_query(
        "SELECT id, user_id, status, error_code FROM public.orders WHERE id = 88213"
    )
    assert result["ok"] is True
    assert result["data"]["row_count"] == 1
    row = result["data"]["rows"][0]
    assert row["status"] == "FAILED"
    assert row["error_code"] == "GATEWAY_502"
    assert "executed_sql" in result["data"]


async def test_db_run_query_rejects_write_end_to_end(db_env):
    from investigation_server.database.tools import db_run_query

    result = await db_run_query("DELETE FROM public.orders WHERE id = 88213")
    assert result["ok"] is False
    assert result["error"]["rule"] in ("blocked_operation", "root_not_select")


async def test_db_search_by_identifier_finds_order_and_payment(db_env):
    from investigation_server.database.tools import db_search_by_identifier

    result = await db_search_by_identifier("88213", "order_id")
    assert result["ok"] is True
    tables_hit = {m["table"] for m in result["data"]["matches"]}
    assert tables_hit == {"public.orders", "public.payments"}


async def test_db_sample_rows_masks_pii(db_env):
    from investigation_server.database.tools import db_sample_rows

    result = await db_sample_rows("public.users", limit=5)
    assert result["ok"] is True
    for row in result["data"]["rows"]:
        assert row["email"] == "[REDACTED]"
