import inspect
import json
import threading
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache, wraps
from pathlib import Path
from typing import Any, Literal, TypeVar
from uuid import uuid4

from investigation_server.config import Settings, get_settings
from investigation_server.errors import ToolError
from investigation_server.logging_config import get_logger
from investigation_server.redaction import redact_arguments

logger = get_logger("audit")

F = TypeVar("F", bound=Callable[..., dict])

# One context id per server process. Each stdio session is its own process
# (design doc §4.3's investigation_start / per-ticket context id is out of
# scope for this build), so this is a reasonable stand-in.
_SESSION_CONTEXT_ID = str(uuid4())


def get_context_id() -> str:
    return _SESSION_CONTEXT_ID


Outcome = Literal["success", "error", "denied"]


@dataclass
class AuditRecord:
    timestamp: str
    context_id: str
    tool: str
    arguments: dict[str, Any]
    rows_returned: int | None
    bytes_scanned: int | None
    duration_ms: float
    outcome: Outcome
    error_rule: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)


class AuditLogger:
    def __init__(self, path: Path):
        self._path = path
        self._lock = threading.Lock()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        logger.info("AuditLogger initialized targeting %s", self._path)

    def write(self, record: AuditRecord) -> None:
        line = record.to_json()
        logger.debug("Writing audit record for tool '%s' (outcome=%s)", record.tool, record.outcome)
        with self._lock:
            try:
                with open(self._path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
                    f.flush()
            except Exception as e:
                logger.error("Failed to write audit record to %s: %s", self._path, e, exc_info=True)


@lru_cache
def get_audit_logger(settings: Settings | None = None) -> AuditLogger:
    return AuditLogger((settings or get_settings()).audit_log_path)


def _extract_meta(result: Any) -> tuple[int | None, int | None]:
    if not isinstance(result, dict):
        return None, None
    meta = result.get("meta")
    if not isinstance(meta, dict):
        return None, None
    return meta.get("row_count"), meta.get("bytes_scanned")


def _record_call(tool_name: str, kwargs: dict, outcome: Outcome, error_rule: str | None, started: float, result: Any) -> None:
    duration_ms = (time.perf_counter() - started) * 1000
    rows_returned, bytes_scanned = _extract_meta(result)
    get_audit_logger().write(
        AuditRecord(
            timestamp=datetime.now(UTC).isoformat(),
            context_id=get_context_id(),
            tool=tool_name,
            arguments=redact_arguments(kwargs),
            rows_returned=rows_returned,
            bytes_scanned=bytes_scanned,
            duration_ms=round(duration_ms, 2),
            outcome=outcome,
            error_rule=error_rule,
        )
    )


def audited(tool_name: str) -> Callable[[F], F]:
    """Wraps a tool function to write exactly one AuditRecord per call.

    Placed directly under @mcp.tool() so it sees the raw arguments/return
    value before FastMCP's own serialization. Uses functools.wraps so
    inspect.signature (which FastMCP's schema builder relies on) still
    resolves the original parameters via __wrapped__. Works on both sync
    (CloudWatch, boto3-backed) and async (database, asyncpg-backed) tools.
    """

    def decorator(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                started = time.perf_counter()
                outcome: Outcome = "success"
                error_rule: str | None = None
                result: Any = None
                try:
                    result = await func(*args, **kwargs)
                    if isinstance(result, dict) and result.get("ok") is False:
                        outcome = "denied"
                        error_rule = (result.get("error") or {}).get("rule")
                    return result
                except ToolError as e:
                    outcome = "denied"
                    error_rule = e.rule
                    raise
                except Exception as e:
                    outcome = "error"
                    error_rule = type(e).__name__
                    raise
                finally:
                    _record_call(tool_name, kwargs, outcome, error_rule, started, result)

            return async_wrapper  # type: ignore[return-value]

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            started = time.perf_counter()
            outcome: Outcome = "success"
            error_rule: str | None = None
            result: Any = None
            try:
                result = func(*args, **kwargs)
                if isinstance(result, dict) and result.get("ok") is False:
                    outcome = "denied"
                    error_rule = (result.get("error") or {}).get("rule")
                return result
            except ToolError as e:
                outcome = "denied"
                error_rule = e.rule
                raise
            except Exception as e:
                outcome = "error"
                error_rule = type(e).__name__
                raise
            finally:
                _record_call(tool_name, kwargs, outcome, error_rule, started, result)

        return wrapper  # type: ignore[return-value]

    return decorator
