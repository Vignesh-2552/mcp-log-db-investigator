import json
import time
from typing import Any

from fastmcp.server.dependencies import get_access_token
from fastmcp.server.middleware import Middleware, MiddlewareContext

from opsinsight.core.logging_config import get_logger
from opsinsight.core.redaction import redact_text

# Dedicated logger name so operators can route audit events separately
# (e.g. a handler on "investigation_server.audit" writing to a SIEM).
audit_logger = get_logger("audit")

# Per-argument cap so one huge query can't flood the audit stream.
MAX_AUDIT_ARG_CHARS = 2000


def _actor() -> str:
    """Who made the call. HTTP requests carry the verified token's client_id;
    stdio and unauthenticated loopback HTTP have no identity to record."""
    token = get_access_token()
    return token.client_id if token is not None else "unauthenticated"


def _safe_args(arguments: dict[str, Any] | None) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in (arguments or {}).items():
        if isinstance(value, str):
            text = redact_text(value)
            if len(text) > MAX_AUDIT_ARG_CHARS:
                text = f"{text[:MAX_AUDIT_ARG_CHARS]}...[truncated {len(text)} chars]"
            safe[key] = text
        else:
            safe[key] = value
    return safe


def _request_id(context: MiddlewareContext) -> str | None:
    try:
        return context.fastmcp_context.request_id if context.fastmcp_context else None
    except RuntimeError:  # no MCP request context (e.g. direct in-process call)
        return None


def _emit(event: dict[str, Any]) -> None:
    # json.dumps escapes newlines/control characters in user-supplied values,
    # so a crafted argument can't forge extra audit lines.
    audit_logger.info("%s", json.dumps(event, default=str, sort_keys=True))


class AuditMiddleware(Middleware):
    """One structured audit event per tool call and resource read: actor,
    target, redacted arguments, outcome and duration. Emitted on every path,
    including guardrail rejections and exceptions.

    Arguments go through the same PII/secret redaction as tool output and
    are truncated; tokens and full result payloads are never logged. Events go
    to the stderr logging pipeline — a failure there can't block a read-only
    query, and Python's logging never raises into the caller."""

    async def on_call_tool(self, context: MiddlewareContext, call_next):
        started = time.perf_counter()
        event: dict[str, Any] = {
            "event": "tool_call",
            "tool": context.message.name,
            "actor": _actor(),
            "request_id": _request_id(context),
            "arguments": _safe_args(context.message.arguments),
            "timestamp": context.timestamp.isoformat(),
        }
        try:
            result = await call_next(context)
        except Exception as e:
            event.update(outcome="exception", error_type=type(e).__name__)
            raise
        else:
            content = getattr(result, "structured_content", None) or {}
            if content.get("ok") is False:
                event.update(outcome="rejected", rule=(content.get("error") or {}).get("rule"))
            else:
                event.update(outcome="ok", row_count=(content.get("meta") or {}).get("row_count"))
            return result
        finally:
            event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            _emit(event)

    async def on_read_resource(self, context: MiddlewareContext, call_next):
        started = time.perf_counter()
        event: dict[str, Any] = {
            "event": "resource_read",
            "uri": redact_text(str(context.message.uri)),
            "actor": _actor(),
            "request_id": _request_id(context),
            "timestamp": context.timestamp.isoformat(),
        }
        try:
            result = await call_next(context)
        except Exception as e:
            event.update(outcome="exception", error_type=type(e).__name__)
            raise
        else:
            event["outcome"] = "ok"
            return result
        finally:
            event["duration_ms"] = round((time.perf_counter() - started) * 1000, 2)
            _emit(event)
