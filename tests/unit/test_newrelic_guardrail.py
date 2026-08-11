import pytest

from core.errors import NewRelicGuardrailError
from integrations.newrelic.guardrail import validate_nrql


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
