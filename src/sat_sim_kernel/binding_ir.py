"""Operations-object to simulation-model binding intermediate representation."""
from __future__ import annotations

from dataclasses import dataclass
from collections.abc import Sequence

from .errors import DuplicateIdError, KernelValidationError
from .identifiers import LocalId, ModelPath, ObjectRef
from .serialization import content_sha256
from .value_types import BindingKind


def _unique(items: Sequence[str], label: str) -> None:
    if len(set(items)) != len(items):
        duplicates = sorted({item for item in items if items.count(item) > 1})
        raise DuplicateIdError(f"duplicate {label}: {', '.join(duplicates)}")


@dataclass(frozen=True)
class ExpressionSpec:
    """Declarative expression reference; execution is owned outside the kernel."""

    expression: str
    language: str = "safe_expr_v1"
    variables: tuple[str, ...] = ("value",)

    def __post_init__(self) -> None:
        if not self.expression.strip():
            raise KernelValidationError("binding expression must be non-empty")
        if not self.language.strip():
            raise KernelValidationError("binding expression language must be non-empty")
        _unique(list(self.variables), "expression variables")


@dataclass(frozen=True)
class FieldMapping:
    source_name: LocalId
    target_name: LocalId
    expression: ExpressionSpec | None = None


@dataclass(frozen=True)
class PropertyBinding:
    binding_id: LocalId
    object_property: ObjectRef
    source: ModelPath
    expression: ExpressionSpec | None = None
    kind: BindingKind = BindingKind.PROPERTY

    def __post_init__(self) -> None:
        if self.kind != BindingKind.PROPERTY:
            raise KernelValidationError("PropertyBinding.kind must be 'property'")


@dataclass(frozen=True)
class ActionBinding:
    binding_id: LocalId
    object_action: ObjectRef
    target_input: ModelPath
    parameter_mappings: tuple[FieldMapping, ...] = ()
    value_expression: ExpressionSpec | None = None
    kind: BindingKind = BindingKind.ACTION

    def __post_init__(self) -> None:
        if self.kind != BindingKind.ACTION:
            raise KernelValidationError("ActionBinding.kind must be 'action'")
        _unique([str(item.target_name) for item in self.parameter_mappings], f"action target fields in {self.binding_id}")


@dataclass(frozen=True)
class EffectBinding:
    binding_id: LocalId
    object_action: ObjectRef
    effect_id: LocalId
    target_node: LocalId
    parameter_mappings: tuple[FieldMapping, ...] = ()
    kind: BindingKind = BindingKind.EFFECT

    def __post_init__(self) -> None:
        if self.kind != BindingKind.EFFECT:
            raise KernelValidationError("EffectBinding.kind must be 'effect'")
        _unique([str(item.target_name) for item in self.parameter_mappings], f"effect target fields in {self.binding_id}")


@dataclass(frozen=True)
class EvidenceBinding:
    binding_id: LocalId
    evidence_id: LocalId
    source: ModelPath
    destination: ObjectRef
    expression: ExpressionSpec | None = None
    kind: BindingKind = BindingKind.EVIDENCE

    def __post_init__(self) -> None:
        if self.kind != BindingKind.EVIDENCE:
            raise KernelValidationError("EvidenceBinding.kind must be 'evidence'")


@dataclass(frozen=True)
class BindingSet:
    binding_set_id: LocalId
    properties: tuple[PropertyBinding, ...] = ()
    actions: tuple[ActionBinding, ...] = ()
    effects: tuple[EffectBinding, ...] = ()
    evidence: tuple[EvidenceBinding, ...] = ()

    def __post_init__(self) -> None:
        all_bindings = [*self.properties, *self.actions, *self.effects, *self.evidence]
        _unique([str(item.binding_id) for item in all_bindings], "binding IDs")

    @property
    def content_sha256(self) -> str:
        return content_sha256(self)


__all__ = [
    "ActionBinding",
    "BindingSet",
    "EffectBinding",
    "EvidenceBinding",
    "ExpressionSpec",
    "FieldMapping",
    "PropertyBinding",
]
