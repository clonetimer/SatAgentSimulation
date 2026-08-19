"""Engine-independent model definition contracts."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Mapping, Sequence
from typing import Any

from .errors import DuplicateIdError, KernelValidationError, ReferenceResolutionError
from .identifiers import LocalId, ModelPath, ModelRef
from .serialization import canonical_json, content_sha256
from .value_types import DataType, EffectKind, FrameRef, ModelKind, PortDirection, TimeSemantics


def _unique(items: Sequence[str], label: str) -> None:
    seen: set[str] = set()
    duplicates: list[str] = []
    for item in items:
        if item in seen and item not in duplicates:
            duplicates.append(item)
        seen.add(item)
    if duplicates:
        raise DuplicateIdError(f"duplicate {label}: {', '.join(duplicates)}")


def _validate_shape(shape: tuple[int, ...]) -> None:
    if any(not isinstance(item, int) or isinstance(item, bool) or item <= 0 for item in shape):
        raise KernelValidationError("shape dimensions must be positive integers")


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def validate_value(data_type: DataType, value: Any, *, choices: tuple[Any, ...] = ()) -> None:
    if data_type == DataType.NUMBER and not _is_number(value):
        raise KernelValidationError(f"expected number, got {type(value).__name__}")
    if data_type == DataType.INTEGER and (not isinstance(value, int) or isinstance(value, bool)):
        raise KernelValidationError(f"expected integer, got {type(value).__name__}")
    if data_type == DataType.BOOLEAN and not isinstance(value, bool):
        raise KernelValidationError(f"expected boolean, got {type(value).__name__}")
    if data_type == DataType.STRING and not isinstance(value, str):
        raise KernelValidationError(f"expected string, got {type(value).__name__}")
    if data_type == DataType.VECTOR:
        if not isinstance(value, (tuple, list)) or not value or not all(_is_number(v) for v in value):
            raise KernelValidationError("expected a non-empty numeric vector")
    if data_type == DataType.ARRAY and not isinstance(value, (tuple, list)):
        raise KernelValidationError("expected an array")
    if data_type == DataType.OBJECT:
        if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
            raise KernelValidationError("expected an object with string keys")
    if data_type == DataType.ENUM:
        if not choices:
            raise KernelValidationError("enum values require non-empty choices")
        encoded = canonical_json(value)
        if encoded not in {canonical_json(item) for item in choices}:
            raise KernelValidationError(f"enum value {value!r} is not in choices")


@dataclass(frozen=True)
class ParameterSpec:
    name: LocalId
    data_type: DataType
    unit: str | None = None
    required: bool = False
    default_is_set: bool = False
    default: Any = None
    minimum: float | None = None
    maximum: float | None = None
    choices: tuple[Any, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        if self.required and self.default_is_set:
            raise KernelValidationError(f"required parameter {self.name} must not define a default")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise KernelValidationError(f"parameter {self.name} minimum exceeds maximum")
        if self.data_type == DataType.ENUM and not self.choices:
            raise KernelValidationError(f"enum parameter {self.name} requires choices")
        if len({canonical_json(item) for item in self.choices}) != len(self.choices):
            raise KernelValidationError(f"parameter {self.name} contains duplicate choices")
        if self.default_is_set:
            self.validate(self.default)

    def validate(self, value: Any) -> None:
        validate_value(self.data_type, value, choices=self.choices)
        if _is_number(value):
            numeric = float(value)
            if self.minimum is not None and numeric < self.minimum:
                raise KernelValidationError(f"parameter {self.name} is below minimum")
            if self.maximum is not None and numeric > self.maximum:
                raise KernelValidationError(f"parameter {self.name} exceeds maximum")


@dataclass(frozen=True)
class PortSpec:
    name: LocalId
    direction: PortDirection
    data_type: DataType
    unit: str | None = None
    frame: FrameRef | None = None
    time_semantics: TimeSemantics = TimeSemantics.DISCRETE
    shape: tuple[int, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _validate_shape(self.shape)


@dataclass(frozen=True)
class StateSpec:
    name: LocalId
    data_type: DataType
    unit: str | None = None
    frame: FrameRef | None = None
    time_semantics: TimeSemantics = TimeSemantics.CONTINUOUS
    shape: tuple[int, ...] = ()
    initial_value_is_set: bool = False
    initial_value: Any = None
    description: str = ""

    def __post_init__(self) -> None:
        _validate_shape(self.shape)
        if self.initial_value_is_set:
            validate_value(self.data_type, self.initial_value)


@dataclass(frozen=True)
class EvidenceSpec:
    evidence_id: LocalId
    source: ModelPath
    data_type: DataType
    unit: str | None = None
    required: bool = True
    description: str = ""


@dataclass(frozen=True)
class EffectSpec:
    effect_id: LocalId
    kind: EffectKind
    parameters: tuple[ParameterSpec, ...] = ()
    target_patterns: tuple[str, ...] = ()
    evidence_ids: tuple[LocalId, ...] = ()
    description: str = ""

    def __post_init__(self) -> None:
        _unique([str(item.name) for item in self.parameters], f"effect parameter in {self.effect_id}")
        _unique([str(item) for item in self.evidence_ids], f"effect evidence in {self.effect_id}")
        if any(not isinstance(pattern, str) or not pattern.strip() for pattern in self.target_patterns):
            raise KernelValidationError(f"effect {self.effect_id} contains an empty target pattern")


@dataclass(frozen=True)
class ModelDefinition:
    model_ref: ModelRef
    kind: ModelKind
    title: str
    description: str = ""
    parameters: tuple[ParameterSpec, ...] = ()
    inputs: tuple[PortSpec, ...] = ()
    outputs: tuple[PortSpec, ...] = ()
    states: tuple[StateSpec, ...] = ()
    effects: tuple[EffectSpec, ...] = ()
    evidence: tuple[EvidenceSpec, ...] = ()
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.title, str) or not self.title.strip():
            raise KernelValidationError("model title must be non-empty")
        _unique([str(item.name) for item in self.parameters], "model parameters")
        _unique([str(item.name) for item in self.inputs], "model inputs")
        _unique([str(item.name) for item in self.outputs], "model outputs")
        _unique([str(item.name) for item in self.states], "model states")
        _unique([str(item.effect_id) for item in self.effects], "model effects")
        _unique([str(item.evidence_id) for item in self.evidence], "model evidence")
        _unique(list(self.tags), "model tags")
        overlap = {str(item.name) for item in self.inputs} & {str(item.name) for item in self.outputs}
        if overlap:
            raise DuplicateIdError(f"input/output port names overlap: {', '.join(sorted(overlap))}")
        wrong_inputs = [str(item.name) for item in self.inputs if item.direction != PortDirection.INPUT]
        wrong_outputs = [str(item.name) for item in self.outputs if item.direction != PortDirection.OUTPUT]
        if wrong_inputs or wrong_outputs:
            raise KernelValidationError(
                f"port direction mismatch; inputs={wrong_inputs or 'ok'}, outputs={wrong_outputs or 'ok'}"
            )
        evidence_ids = {str(item.evidence_id) for item in self.evidence}
        for effect in self.effects:
            missing = [str(item) for item in effect.evidence_ids if str(item) not in evidence_ids]
            if missing:
                raise ReferenceResolutionError(
                    f"effect {effect.effect_id} references unknown evidence: {', '.join(missing)}"
                )

    def parameter(self, name: str) -> ParameterSpec:
        for item in self.parameters:
            if str(item.name) == name:
                return item
        raise ReferenceResolutionError(f"unknown parameter {name!r} in {self.model_ref}")

    def input_port(self, name: str) -> PortSpec:
        for item in self.inputs:
            if str(item.name) == name:
                return item
        raise ReferenceResolutionError(f"unknown input port {name!r} in {self.model_ref}")

    def output_port(self, name: str) -> PortSpec:
        for item in self.outputs:
            if str(item.name) == name:
                return item
        raise ReferenceResolutionError(f"unknown output port {name!r} in {self.model_ref}")

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)


__all__ = [
    "EffectSpec",
    "EvidenceSpec",
    "ModelDefinition",
    "ParameterSpec",
    "PortSpec",
    "StateSpec",
    "validate_value",
]
