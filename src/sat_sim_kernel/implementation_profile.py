"""Engine implementation coverage for one model definition."""
from __future__ import annotations

from dataclasses import dataclass
import re
from collections.abc import Sequence

from .errors import DuplicateIdError, KernelValidationError
from .identifiers import ImplementationId, ModelRef
from .serialization import content_sha256

_KEY = re.compile(r"^[a-z][a-z0-9_]*(?:[.-][a-z0-9_]+)*$")


def _validate_key(value: str, label: str) -> None:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise KernelValidationError(f"invalid {label}: {value!r}")


def _unique(items: Sequence[str], label: str) -> None:
    if len(set(items)) != len(items):
        duplicates = sorted({item for item in items if items.count(item) > 1})
        raise DuplicateIdError(f"duplicate {label}: {', '.join(duplicates)}")


@dataclass(frozen=True)
class ModelImplementationProfile:
    implementation_id: ImplementationId
    model_ref: ModelRef
    engine: str
    fidelity: str
    adapter_key: str
    supported_inputs: tuple[str, ...] = ()
    supported_outputs: tuple[str, ...] = ()
    supported_effects: tuple[str, ...] = ()
    limitations: tuple[str, ...] = ()
    runtime_requirements: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _validate_key(self.engine, "engine")
        _validate_key(self.fidelity, "fidelity")
        _validate_key(self.adapter_key, "adapter_key")
        for label, items in (
            ("supported inputs", self.supported_inputs),
            ("supported outputs", self.supported_outputs),
            ("supported effects", self.supported_effects),
            ("limitations", self.limitations),
            ("runtime requirements", self.runtime_requirements),
        ):
            if any(not isinstance(item, str) or not item.strip() for item in items):
                raise KernelValidationError(f"{label} must contain non-empty strings")
            _unique(list(items), label)

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)


__all__ = ["ModelImplementationProfile"]
