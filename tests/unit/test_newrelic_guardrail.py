from datetime import UTC, datetime

import pytest

from opsinsight.core.errors import NewRelicGuardrailError
from opsinsight.integrations.newrelic.guardrail import (
    enforce_nrql_window,
    validate_nrql,
)


def test_valid_select_passes():
    result = validate_nrql("SELECT timestamp, message FROM Log SINCE 1 hour ago", max_rows=500)
    assert result.upper().startswith("SELECT")
    assert "LIMIT 500" in result.upper()


def test_empty_query_rejected():
    with pytest.raises(NewRelicGuardrailError) as exc:
        validate_nrql("   ", max_rows=500)
    assert exc.value.rule == "empty_query"


def test_multiple_statements_rejected():
    with pytest.raises(NewRelicGuardrailError) as exc:
        validate_nrql("SELECT * FROM Log; SELECT * FROM Metric", max_rows=500)
    assert exc.value.rule == "multiple_statements"


def test_trailing_semicolon_allowed():
    result = validate_nrql("SELECT * FROM Log;", max_rows=500)
    assert result.upper().startswith("SELECT")


def test_non_select_rejected():
    with pytest.raises(NewRelicGuardrailError) as exc:
        validate_nrql("FROM Log SELECT *", max_rows=500)
    assert exc.value.rule == "not_select"


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM Log DELETE",
        "SELECT * FROM Log WHERE 1=1 INSERT INTO x",
        "SELECT * FROM Log CREATE TABLE x",
    ],
)
def test_blocked_keywords_rejected(query):
    with pytest.raises(NewRelicGuardrailError) as exc:
        validate_nrql(query, max_rows=500)
    assert exc.value.rule == "blocked_keyword"


def test_limit_clamped_to_max_rows():
    result = validate_nrql("SELECT * FROM Log LIMIT 10000", max_rows=500)
    assert "LIMIT 500" in result.upper()


def test_limit_injected_when_missing():
    result = validate_nrql("SELECT * FROM Log", max_rows=500)
    assert "LIMIT 500" in result.upper()


def test_requested_limit_respected_when_under_max():
    result = validate_nrql("SELECT * FROM Log", max_rows=500, requested_limit=10)
    assert "LIMIT 10" in result.upper()


def test_requested_limit_cannot_exceed_max_rows():
    result = validate_nrql("SELECT * FROM Log", max_rows=500, requested_limit=999999)
    assert "LIMIT 500" in result.upper()


@pytest.mark.parametrize(
    "query",
    [
        "SELECT message FROM Log WHERE message LIKE '%update failed%'",
        "SELECT message FROM Log WHERE message LIKE '%delete; retry%'",
        'SELECT message FROM Log WHERE action = "DROP"',
    ],
)
def test_keywords_and_semicolons_inside_string_literals_allowed(query):
    assert validate_nrql(query, max_rows=500).startswith(query)


def test_blocked_keyword_outside_literal_still_rejected():
    with pytest.raises(NewRelicGuardrailError) as exc:
        validate_nrql("SELECT * FROM Log WHERE x = 'ok' DELETE", max_rows=500)
    assert exc.value.rule == "blocked_keyword"


def test_limit_inside_string_literal_is_not_treated_as_limit_clause():
    result = validate_nrql("SELECT * FROM Log WHERE message = 'LIMIT 5'", max_rows=500, requested_limit=50)
    assert result == "SELECT * FROM Log WHERE message = 'LIMIT 5' LIMIT 50"


def test_limit_max_is_clamped():
    result = validate_nrql("SELECT * FROM Log LIMIT MAX SINCE 1 hour ago", max_rows=500)
    assert result == "SELECT * FROM Log LIMIT 500 SINCE 1 hour ago"


def test_limit_appended_on_new_line_after_trailing_comment():
    result = validate_nrql("SELECT * FROM Log -- recent errors", max_rows=500)
    assert result == "SELECT * FROM Log -- recent errors\nLIMIT 500"


_NOW = datetime(2026, 10, 28, 12, 0, tzinfo=UTC)


def _window(query: str) -> str:
    return enforce_nrql_window(query, default_hours=24, max_hours=168, now=_NOW)


def test_window_injected_before_limit_when_missing():
    assert _window("SELECT * FROM Log LIMIT 100") == "SELECT * FROM Log SINCE 24 hours ago LIMIT 100"


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM Log SINCE 3 hours ago LIMIT 100",
        "SELECT * FROM Log SINCE 7 days ago",
        "SELECT * FROM Log SINCE yesterday UNTIL today",
        "SELECT * FROM Log SINCE '2026-10-27 12:00:00' UNTIL '2026-10-28 00:00:00'",
        "SELECT * FROM Log SINCE 1793102400000",
        "SELECT count(*) FROM Log FACET level SINCE 30 minutes ago TIMESERIES",
    ],
)
def test_window_within_cap_passes_unchanged(query):
    assert _window(query) == query


@pytest.mark.parametrize(
    "query",
    [
        "SELECT * FROM Log SINCE 8 days ago",
        "SELECT * FROM Log SINCE 2 months ago LIMIT 10",
        "SELECT * FROM Log SINCE '2026-01-01'",
    ],
)
def test_window_over_cap_rejected(query):
    with pytest.raises(NewRelicGuardrailError) as exc:
        _window(query)
    assert exc.value.rule == "window_too_large"


def test_unparseable_since_rejected():
    with pytest.raises(NewRelicGuardrailError) as exc:
        _window("SELECT * FROM Log SINCE this quarter")
    assert exc.value.rule == "unsupported_time_range"


def test_since_after_until_rejected():
    with pytest.raises(NewRelicGuardrailError) as exc:
        _window("SELECT * FROM Log SINCE 1 hour ago UNTIL 2 hours ago")
    assert exc.value.rule == "invalid_window"


def test_since_inside_string_literal_is_ignored():
    query = "SELECT * FROM Log WHERE message = 'SINCE 30 days ago'"
    assert _window(query) == f"{query} SINCE 24 hours ago"
