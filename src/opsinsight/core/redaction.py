import re
from collections.abc import Mapping
from typing import Any

from opsinsight.core.config import Settings, get_settings

# Order matters: more specific patterns before more general ones.
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("EMAIL", re.compile(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}")),
    ("CARD", re.compile(r"\b(?:\d[ -]?){13,19}\b")),
    ("AADHAAR", re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")),
    ("PAN", re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b")),
    ("PHONE", re.compile(r"\b(?:\+?\d{1,3}[\s-]?)?\d{10}\b")),
]

# Substrings the PII patterns would wrongly mask, protected with placeholders
# before running the pattern list:
# - UUIDs are hyphen-delimited digit runs, so CARD/AADHAAR can partially match
#   inside one (a hyphen is a non-word char, so \b fires right after it).
# - Unix epoch timestamps: 10-digit seconds match PHONE and 13-digit millis
#   match CARD, which would blank out the timestamps needed to correlate
#   events. Only runs starting with "1" (2001-2033) are protected; phone
#   numbers (US, India) and Aadhaar numbers never start with 1, and neither
#   do 13-digit card numbers.
_PROTECTED_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
    r"|\b1\d{9}(?:\d{3})?\b"
)

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


def enabled(settings: Settings | None = None) -> bool:
    return (settings or get_settings()).pii_redaction


def redact_text(value: str) -> str:
    originals: list[str] = []

    def _protect(match: re.Match[str]) -> str:
        originals.append(match.group())
        return f"\x00P{len(originals) - 1}\x00"

    protected = _PROTECTED_RE.sub(_protect, value)
    for label, pattern in _PATTERNS:
        protected = pattern.sub(f"[REDACTED_{label}]", protected)
    if not originals:
        return protected
    return re.sub(r"\x00P(\d+)\x00", lambda m: originals[int(m.group(1))], protected)


def redact_row(row: dict[str, Any], settings: Settings | None = None) -> dict[str, Any]:
    if not enabled(settings):
        return row
    return _redact_mapping(row)


def _redact_mapping(value: Mapping[Any, Any]) -> dict[Any, Any]:
    redacted: dict[Any, Any] = {}
    for key, item in value.items():
        if isinstance(key, str) and key.lower() in PII_COLUMN_NAMES:
            redacted[key] = "[REDACTED]" if item is not None else None
        else:
            redacted[key] = _redact_value(item)
    return redacted


def _redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return _redact_mapping(value)
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(_redact_value(item) for item in value)
    return value


def redact_rows(rows: list[dict[str, Any]], settings: Settings | None = None) -> list[dict[str, Any]]:
    return [redact_row(row, settings) for row in rows]


def redact_log_event(message: str, settings: Settings | None = None) -> str:
    if not enabled(settings) or not isinstance(message, str):
        return message
    return redact_text(message)
