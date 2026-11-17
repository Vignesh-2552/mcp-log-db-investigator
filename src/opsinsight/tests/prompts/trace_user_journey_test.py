from opsinsight.prompts.trace_user_journey import trace_user_journey


def test_auto_id_type_asks_the_model_to_find_the_column():
    text = trace_user_journey("ord_89214")
    assert "Identifier type: `auto`" in text
    assert "Work out which column holds this identifier" in text
    # trace_request skips the DB without db_id_type; the prompt must say so.
    assert "only\n   searches the database when `db_id_type` is given" in text


def test_explicit_id_type_is_passed_as_db_id_type():
    text = trace_user_journey("req-99a8b7", id_type="request_id", time_window="6h")
    assert 'db_id_type="request_id"' in text
    assert "Time window: `6h`" in text


def test_db_rows_are_not_presented_as_change_history():
    text = trace_user_journey("user_342")
    assert "`in_window`" in text
    assert "not its\n   change history" in text


def test_journey_table_columns():
    text = trace_user_journey("user_342")
    assert "`Timestamp (UTC) | Source | Service / table | Action or state | Status | Details`" in text
