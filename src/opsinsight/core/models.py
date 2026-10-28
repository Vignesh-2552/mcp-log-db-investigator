from dataclasses import asdict, dataclass


@dataclass
class DictableMixin:
    """Mixin for response-shape dataclasses across all three sources. `asdict()`
    recurses through nested dataclasses and lists of them, so calling `.to_dict()`
    once on the outermost object also serializes nested dataclass fields — no
    manual per-field conversion needed at the service-layer boundary."""

    def to_dict(self) -> dict:
        return asdict(self)
