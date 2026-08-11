import json

import pytest

pytestmark = pytest.mark.integration


@pytest.fixture
def db_env(settings_override, tmp_path):
    settings_override(
        audit_log_path=str(tmp_path / "audit.jsonl"),
    )
    return tmp_path / "audit.jsonl"


def test_db_list_tables_returns_tables(db_env):
    from investigation_server.database.tools import db_list_tables

    result = db_list_tables()
    assert result["ok"] is True
    assert len(result["data"]["tables"]) > 0


def test_db_describe_table_returns_columns(db_env):
    from investigation_server.database.tools import db_describe_table

    result = db_describe_table("public.orders")
    assert result["ok"] is True
    col_names = {c["name"] for c in result["data"]["columns"]}
    assert {"id", "user_id", "status", "error_code", "created_at"} <= col_names
    assert result["data"]["primary_key"] == ["id"]


def test_db_run_query_returns_seeded_failed_order(db_env):
    from investigation_server.database.tools import db_run_query

    result = db_run_query(
        "SELECT id, user_id, status, error_code FROM public.orders WHERE id = 88213"
    )
    assert result["ok"] is True
    assert result["data"]["row_count"] == 1
    row = result["data"]["rows"][0]
    assert row["status"] == "FAILED"
    assert row["error_code"] == "GATEWAY_502"
    assert "executed_sql" in result["data"]


def test_db_run_query_rejects_write_end_to_end(db_env):
    from investigation_server.database.tools import db_run_query

    result = db_run_query("DELETE FROM public.orders WHERE id = 88213")
    assert result["ok"] is False
    assert result["error"]["rule"] in ("blocked_operation", "root_not_select")




def test_db_search_by_identifier_finds_order_and_payment(db_env):
    from investigation_server.database.tools import db_search_by_identifier

    result = db_search_by_identifier("88213", "order_id")
    assert result["ok"] is True
    tables_hit = {m["table"] for m in result["data"]["matches"]}
    assert tables_hit == {"public.orders", "public.payments"}


def test_db_sample_rows_masks_pii(db_env):
    from investigation_server.database.tools import db_sample_rows

    result = db_sample_rows("public.users", limit=5)
    assert result["ok"] is True
    for row in result["data"]["rows"]:
        assert row["email"] == "[REDACTED]"


def test_audit_log_records_each_call(db_env):
    from investigation_server.database.tools import db_list_tables, db_run_query

    db_list_tables()
    db_run_query("DELETE FROM public.orders")  # denied

    lines = db_env.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(line) for line in lines]
    outcomes = [r["outcome"] for r in records]
    assert "success" in outcomes
    assert "denied" in outcomes
    for r in records:
        assert "context_id" in r and r["context_id"]
