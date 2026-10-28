import pytest

from opsinsight.core.config import Settings
from opsinsight.core.redaction import redact_log_event, redact_row, redact_text

# Synthetic credential-shaped fixtures, assembled at runtime so no literal in
# the source matches a secret scanner's pattern (GitHub push protection
# ignores gitleaks:allow comments).
_FAKE_AWS_KEY_ID = "AKIA" + "ABCDEFGHIJKLMNOP"
_FAKE_NR_KEY = "NRAK-" + "ABCDEFGHIJKLMNOPQRSTUVWXYZ1"
_FAKE_JWT = "eyJhbGciOiJIUzI1NiJ9" + "." + "eyJzdWIiOiIxIn0" + "." + "c2lnbmF0dXJl"

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


def test_epoch_seconds_timestamp_is_not_redacted():
    text = '{"ts":1786538460,"level":"error"}'
    assert redact_text(text) == text


def test_epoch_millis_timestamp_is_not_redacted():
    text = "event at 1786538460000 failed"
    assert redact_text(text) == text


def test_phone_number_still_redacted_next_to_timestamp():
    result = redact_text("ts=1786538460 phone=9876543210")
    assert result == "ts=1786538460 phone=[REDACTED_PHONE]"


def test_card_number_still_redacted_next_to_timestamp():
    result = redact_text("ts=1786538460000 card=4111111111111111")
    assert result == "ts=1786538460000 card=[REDACTED_CARD]"


@pytest.mark.parametrize(
    ("text", "leaked"),
    [
        ('{"user":"a","password":"hunter2 x"}', "hunter2"),
        ("db_password=s3cr3t&next=1", "s3cr3t"),
        ("client_secret: abcdef123456", "abcdef123456"),  # gitleaks:allow (synthetic fixture)
        ("Authorization: Bearer abc.def-ghi_jkl", "abc.def-ghi_jkl"),
        (f"call failed with bearer {_FAKE_JWT}", "eyJhbGci"),
        (f"key {_FAKE_AWS_KEY_ID} used", _FAKE_AWS_KEY_ID),
        (f"nr key {_FAKE_NR_KEY} rejected", _FAKE_NR_KEY),
        ("-----BEGIN RSA PRIVATE KEY-----\nMIIEow\n-----END RSA PRIVATE KEY-----", "MIIEow"),
    ],
)
def test_secrets_in_text_are_redacted(text, leaked):
    result = redact_text(text)
    assert leaked not in result
    assert "[REDACTED_SECRET]" in result


def test_secret_key_value_keeps_key_and_quotes():
    assert redact_text('{"api_key": "abc123"}') == '{"api_key": "[REDACTED_SECRET]"}'


def test_non_secret_keys_untouched():
    text = "token_count=42 status=ok"
    assert redact_text(text) == text


@pytest.mark.parametrize(
    "column", ["api_key", "client_secret", "stripe_token", "db_password", "Private_Key", "session_cookie"]
)
def test_sensitive_column_names_masked_by_substring(column):
    assert redact_row({column: "value"}, _ENABLED)[column] == "[REDACTED]"


def test_redact_row_still_masks_pii_column_names():
    row = {"id": "00000000-0000-0000-0000-000000000000", "email": "someone@example.com"}
    result = redact_row(row, _ENABLED)
    assert result["id"] == "00000000-0000-0000-0000-000000000000"
    assert result["email"] == "[REDACTED]"


def test_redact_row_recurses_into_structured_values():
    row = {
        "payload": {
            "email": "nested@example.com",
            "profile": {"note": "contact nested@example.com"},
            "items": ["4111 1111 1111 1111", {"access_token": "secret"}],
        },
        "values": ("person@example.com",),
    }

    result = redact_row(row, _ENABLED)

    assert result["payload"]["email"] == "[REDACTED]"
    assert result["payload"]["profile"]["note"] == "contact [REDACTED_EMAIL]"
    assert result["payload"]["items"] == ["[REDACTED_CARD]", {"access_token": "[REDACTED]"}]
    assert result["values"] == ("[REDACTED_EMAIL]",)


def test_redaction_disabled_preserves_nested_object_identity():
    row = {"payload": {"email": "nested@example.com"}}

    assert redact_row(row, _DISABLED) is row


def test_redact_log_event_preserves_uuid_inside_json_text():
    message = '{"user":{"customerId":"00000000-0000-0000-0000-000000000000"}}'
    assert redact_log_event(message, _ENABLED) == message


def test_redaction_disabled_passes_through_unchanged():
    message = "card 4111 1111 1111 1111 uuid 00000000-0000-0000-0000-000000000000"
    assert redact_log_event(message, _DISABLED) == message
