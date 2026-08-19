"""Minimal O3 operations-object contracts used by the A3R vertical slice."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from sat_sim_kernel import DataType, KernelValidationError, ObjectRef, content_sha256


def _unique(values: Sequence[str], label: str) -> None:
    if len(values) != len(set(values)):
        raise KernelValidationError(f"duplicate {label}")


@dataclass(frozen=True)
class ObjectPropertySpec:
    ref: ObjectRef
    data_type: DataType
    unit: str | None = None
    description: str = ""


@dataclass(frozen=True)
class ObjectActionSpec:
    ref: ObjectRef
    parameter_names: tuple[str, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if any(not item.strip() for item in self.parameter_names):
            raise KernelValidationError("object action parameter names must be non-empty")
        _unique(list(self.parameter_names), "object action parameters")


@dataclass(frozen=True)
class ObjectEvidenceSpec:
    ref: ObjectRef
    data_type: DataType
    description: str = ""


@dataclass(frozen=True)
class OperationsObjectDefinition:
    object_ref: ObjectRef
    title: str
    properties: tuple[ObjectPropertySpec, ...] = ()
    actions: tuple[ObjectActionSpec, ...] = ()
    effects: tuple[ObjectActionSpec, ...] = ()
    evidence: tuple[ObjectEvidenceSpec, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if not self.title.strip():
            raise KernelValidationError("operations object title must be non-empty")
        refs = [
            *(str(item.ref) for item in self.properties),
            *(str(item.ref) for item in self.actions),
            *(str(item.ref) for item in self.effects),
            *(str(item.ref) for item in self.evidence),
        ]
        _unique(refs, "operations object members")
        prefix = str(self.object_ref) + "."
        if any(not ref.startswith(prefix) for ref in refs):
            raise KernelValidationError("operations object members must be children of object_ref")

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)


__all__ = [
    "ObjectActionSpec",
    "ObjectEvidenceSpec",
    "ObjectPropertySpec",
    "OperationsObjectDefinition",
]
