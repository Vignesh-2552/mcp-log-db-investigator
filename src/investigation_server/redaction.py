import re
from typing import Any

from investigation_server.config import Settings, get_settings

# Order matters: more specific patterns before more general ones.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?\d{10}\b")),
]

PII_COLUMN_NAMES = {
    "email",
    "email_address",
    "phone",
    "phone_number",
    "mobile",
    "pan",
    "pan_number",
    "aadhaar",
    "aadhaar_number",
    "card_number",
    "cvv",
    "ssn",
    "token",
    "access_token",
    "refresh_token",
    "password",
    "password_hash",
}

SECRET_ARG_NAMES = {"password", "token", "secret", "api_key", "authorization", "access_token"}


def enabled(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).pii_redaction


def redact_text(value: str) -> str:
    for label, pattern in _PATTERNS:
        value = pattern.sub(f"[REDACTED_{label}]", value)
    return value


def redact_row(row: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    if not enabled(settings):
        return row
    redacted: dict[str, Any] = {}
    for key, value in row.items():
        if key.lower() in PII_COLUMN_NAMES:
            redacted[key] = "[REDACTED]" if value is not None else None
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        else:
            redacted[key] = value
    return redacted


def redact_rows(rows: list[dict[str, Any]], settings: Settings | None = None) -> list[dict[str, Any]]:
    return [redact_row(row, settings) for row in rows]


def redact_log_event(message: str, settings: Settings | None = None) -> str:
    if not enabled(settings):
        return message
    return redact_text(message)


def redact_arguments(kwargs: dict[str, Any]) -> dict[str, Any]:
    """Redact tool-call arguments before they are written to the audit log.

    Applied unconditionally (independent of PII_REDACTION) so the audit
    log never leaks raw PII/secrets even if response-path redaction is
    toggled off.
    """
    redacted: dict[str, Any] = {}
    for key, value in kwargs.items():
        if key.lower() in SECRET_ARG_NAMES:
            redacted[key] = "[REDACTED]"
        elif isinstance(value, str):
            redacted[key] = redact_text(value)
        else:
            redacted[key] = value
    return redacted
