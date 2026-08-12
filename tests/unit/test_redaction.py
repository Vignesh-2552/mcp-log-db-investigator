from core.config import Settings
from core.redaction import redact_log_event, redact_row, redact_text

_ENABLED = Settings(pii_redaction=True)
_DISABLED = Settings(pii_redaction=False)


def test_card_number_space_separated_is_redacted():
    assert redact_text("card: 4111 1111 1111 1111") == "card: [REDACTED_CARD]"


def test_card_number_hyphen_separated_is_redacted():
    assert redact_text("card: 4111-1111-1111-1111") == "card: [REDACTED_CARD]"


def test_nil_uuid_is_not_redacted():
    value = "00000000-0000-0000-0000-000000000000"
    assert redact_text(f"customerId={value}") == f"customerId={value}"


def test_random_looking_uuid_is_not_redacted():
    value = "12345678-1234-5678-9012-345678901234"
    assert redact_text(f"customerId={value}") == f"customerId={value}"


def test_uuid_and_real_card_number_in_same_string():
    uuid = "00000000-0000-0000-0000-000000000000"
    text = f"user {uuid} paid with 4111 1111 1111 1111"
    result = redact_text(text)
    assert uuid in result
    assert "4111 1111 1111 1111" not in result
    assert "[REDACTED_CARD]" in result


def test_duplicate_uuid_appears_twice_and_both_preserved():
    uuid = "abcdef12-3456-7890-abcd-ef1234567890"
    text = f"trace_id={uuid} parent_trace_id={uuid}"
    result = redact_text(text)
    assert result == text
    assert result.count(uuid) == 2


def test_redact_row_still_masks_pii_column_names():
    row = {"id": "00000000-0000-0000-0000-000000000000", "email": "someone@example.com"}
    result = redact_row(row, _ENABLED)
    assert result["id"] == "00000000-0000-0000-0000-000000000000"
    assert result["email"] == "[REDACTED]"


def test_redact_log_event_preserves_uuid_inside_json_text():
    message = '{"user":{"customerId":"00000000-0000-0000-0000-000000000000"}}'
    assert redact_log_event(message, _ENABLED) == message


def test_redaction_disabled_passes_through_unchanged():
    message = "card 4111 1111 1111 1111 uuid 00000000-0000-0000-0000-000000000000"
    assert redact_log_event(message, _DISABLED) == message
