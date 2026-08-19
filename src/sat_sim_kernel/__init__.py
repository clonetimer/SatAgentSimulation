"""Engine-independent kernel for governed satellite simulation model assets.

This top-level package intentionally does not import :mod:`sat_sim`; importing
it must not initialize Basilisk, FastAPI, concrete satellite models, or product
registries.
"""
from __future__ import annotations

from .binding_ir import (
    ActionBinding,
    BindingSet,
    EffectBinding,
    EvidenceBinding,
    ExpressionSpec,
    FieldMapping,
    PropertyBinding,
)
from .errors import (
    DuplicateIdError,
    KernelError,
    KernelValidationError,
    ReferenceResolutionError,
)
from .identifiers import (
    ImplementationId,
    LocalId,
    ModelId,
    ModelPath,
    ModelRef,
    ObjectRef,
)
from .implementation_profile import ModelImplementationProfile
from .model_definition import (
    EffectSpec,
    EvidenceSpec,
    ModelDefinition,
    ParameterSpec,
    PortSpec,
    StateSpec,
    validate_value,
)
from .model_graph import (
    ModelConnection,
    ModelGraphDefinition,
    ModelNode,
    ParameterOverride,
    PortEndpoint,
)
from .serialization import canonical_json, content_sha256, to_canonical_dict
from .value_types import (
    BindingKind,
    ConnectionKind,
    DataType,
    EffectKind,
    FrameRef,
    ModelKind,
    PortDirection,
    TimeSemantics,
)

KERNEL_SCHEMA_VERSION = "sat-sim.model-kernel.v1"

__all__ = [
    "ActionBinding",
    "BindingKind",
    "BindingSet",
    "ConnectionKind",
    "DataType",
    "DuplicateIdError",
    "EffectBinding",
    "EffectKind",
    "EffectSpec",
    "EvidenceBinding",
    "EvidenceSpec",
    "ExpressionSpec",
    "FieldMapping",
    "FrameRef",
    "ImplementationId",
    "KERNEL_SCHEMA_VERSION",
    "KernelError",
    "KernelValidationError",
    "LocalId",
    "ModelConnection",
    "ModelDefinition",
    "ModelGraphDefinition",
    "ModelId",
    "ModelImplementationProfile",
    "ModelKind",
    "ModelNode",
    "ModelPath",
    "ModelRef",
    "ObjectRef",
    "ParameterOverride",
    "ParameterSpec",
    "PortDirection",
    "PortEndpoint",
    "PortSpec",
    "PropertyBinding",
    "ReferenceResolutionError",
    "StateSpec",
    "TimeSemantics",
    "canonical_json",
    "content_sha256",
    "to_canonical_dict",
    "validate_value",
]
