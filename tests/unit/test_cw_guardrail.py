from datetime import UTC, datetime, timedelta

import pytest

from investigation_server.cloudwatch.guardrail import (
    check_bytes_scanned,
    clamp_window,
    poll_query_with_backoff,
    validate_log_groups,
)
from investigation_server.errors import CWGuardrailError

ALLOWLIST = frozenset({"/aws/ecs/checkout-svc", "/aws/ecs/payment-svc"})


def test_validate_log_groups_passes_when_allowed():
    result = validate_log_groups(["/aws/ecs/checkout-svc"], ALLOWLIST)
    assert result == ["/aws/ecs/checkout-svc"]


def test_validate_log_groups_rejects_empty():
    with pytest.raises(CWGuardrailError) as exc:
        validate_log_groups([], ALLOWLIST)
    assert exc.value.rule == "no_log_groups"


def test_validate_log_groups_rejects_disallowed_with_allowed_list():
    with pytest.raises(CWGuardrailError) as exc:
        validate_log_groups(["/aws/ecs/checkout-svc", "/aws/lambda/evil"], ALLOWLIST)
    assert exc.value.rule == "log_group_not_allowed"
    assert exc.value.allowed == sorted(ALLOWLIST)


def test_clamp_window_defaults_to_default_hours():
    start, end = clamp_window(None, None, default_hours=24, max_hours=168)
    assert (end - start) == timedelta(hours=24)


def test_clamp_window_accepts_explicit_window_under_cap():
    end = datetime.now(UTC)
    start = end - timedelta(hours=2)
    got_start, got_end = clamp_window(start.isoformat(), end.isoformat(), default_hours=24, max_hours=168)
    assert got_end - got_start == timedelta(hours=2)


def test_clamp_window_rejects_window_over_hard_cap():
    end = datetime.now(UTC)
    start = end - timedelta(days=10)
    with pytest.raises(CWGuardrailError) as exc:
        clamp_window(start.isoformat(), end.isoformat(), default_hours=24, max_hours=168)
    assert exc.value.rule == "window_too_large"


def test_clamp_window_rejects_start_after_end():
    end = datetime.now(UTC)
    start = end + timedelta(hours=1)
    with pytest.raises(CWGuardrailError) as exc:
        clamp_window(start.isoformat(), end.isoformat(), default_hours=24, max_hours=168)
    assert exc.value.rule == "invalid_window"


def test_clamp_window_rejects_unparseable_timestamp():
    with pytest.raises(CWGuardrailError) as exc:
        clamp_window("not-a-date", None, default_hours=24, max_hours=168)
    assert exc.value.rule == "invalid_timestamp"


def test_check_bytes_scanned_under_ceiling_passes():
    check_bytes_scanned(1000, ceiling=5000)  # should not raise


def test_check_bytes_scanned_over_ceiling_raises():
    with pytest.raises(CWGuardrailError) as exc:
        check_bytes_scanned(6000, ceiling=5000)
    assert exc.value.rule == "bytes_scanned_ceiling_exceeded"


def test_poll_query_with_backoff_returns_on_completion():
    calls = {"n": 0}

    def poll_fn():
        calls["n"] += 1
        if calls["n"] < 3:
            return {"status": "Running"}
        return {"status": "Complete", "results": [1, 2, 3]}

    sleeps: list[float] = []
    result = poll_query_with_backoff(poll_fn, max_wait_s=60, sleep_fn=sleeps.append)

    assert result["status"] == "Complete"
    assert calls["n"] == 3
    assert len(sleeps) == 2  # slept between poll 1->2 and 2->3


def test_poll_query_with_backoff_returns_last_status_when_still_running_at_timeout():
    def poll_fn():
        return {"status": "Running", "query_id": "abc-123"}

    sleeps: list[float] = []
    result = poll_query_with_backoff(poll_fn, max_wait_s=5, sleep_fn=sleeps.append)

    assert result["status"] == "Running"
    assert result["query_id"] == "abc-123"
    assert sum(sleeps) <= 5


def test_poll_query_with_backoff_respects_max_wait_s_budget():
    def poll_fn():
        return {"status": "Running"}

    sleeps: list[float] = []
    poll_query_with_backoff(poll_fn, max_wait_s=3, sleep_fn=sleeps.append)

    assert sum(sleeps) <= 3
