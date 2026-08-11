from dataclasses import dataclass, field


@dataclass
class ToolError(Exception):
    """A structured, self-correctable error surfaced to the client model.

    Carries a machine-readable `rule` (which guardrail fired) plus enough
    detail that the model can adjust its next attempt instead of just
    seeing a bare "denied" (design doc §8).
    """

    rule: str
    message: str
    detail: str | None = None
    allowed: list[str] | None = field(default=None)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.rule}: {self.message}"

    def to_response(self) -> dict:
        return {
            "ok": False,
            "error": {
                "rule": self.rule,
                "message": self.message,
                "detail": self.detail,
                "allowed": self.allowed,
            },
        }


class GuardrailError(ToolError):
    """Raised by the DB guardrail layer (db/guardrail.py)."""


class CWGuardrailError(ToolError):
    """Raised by the CloudWatch guardrail layer (cw/guardrail.py)."""
