from datetime import UTC, datetime, timedelta

import pytest

from opsinsight.core.errors import CWGuardrailError, ToolError
from opsinsight.core.time_window import clamp_window, parse_iso8601
from opsinsight.integrations.cloudwatch import guardrail as cw_guardrail


def test_parse_iso8601_assumes_utc_without_timezone():
    assert parse_iso8601("2026-01-01T10:00:00") == datetime(2026, 1, 1, 10, tzinfo=UTC)


def test_clamp_window_defaults_end_to_now_and_start_to_default_hours():
    start, end = clamp_window(None, None, default_hours=6, max_hours=24)
    assert end - start == timedelta(hours=6)
    assert abs((datetime.now(UTC) - end).total_seconds()) < 5


@pytest.mark.parametrize(
    ("start", "end", "rule"),
    [
        ("2026-01-02T00:00:00+00:00", "2026-01-01T00:00:00+00:00", "invalid_window"),
        ("2026-01-01T00:00:00+00:00", "2026-01-03T00:00:00+00:00", "window_too_large"),
        ("not-a-date", None, "invalid_timestamp"),
    ],
)
def test_clamp_window_raises_plain_tool_error_by_default(start, end, rule):
    with pytest.raises(ToolError) as exc:
        clamp_window(start, end, default_hours=1, max_hours=24)
    assert type(exc.value) is ToolError
    assert exc.value.rule == rule


def test_clamp_window_raises_the_given_error_class():
    with pytest.raises(CWGuardrailError):
        clamp_window("bad", None, default_hours=1, max_hours=24, error_cls=CWGuardrailError)


def test_cloudwatch_guardrail_keeps_raising_its_own_error_class():
    with pytest.raises(CWGuardrailError) as exc:
        cw_guardrail.clamp_window("bad", None, default_hours=1, max_hours=24)
    assert exc.value.rule == "invalid_timestamp"
