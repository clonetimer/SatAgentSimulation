"""Stable identifiers used by the engine-independent model kernel."""
from __future__ import annotations

from dataclasses import dataclass
import re

from .errors import KernelValidationError

_QUALIFIED_ID = re.compile(r"^[a-z][a-z0-9_]*(?:\.[a-z][a-z0-9_]*)+$")
_LOCAL_ID = re.compile(r"^[a-z][a-z0-9_]*(?:[-.][a-z0-9_]+)*$")
_VERSION = re.compile(r"^[0-9]+(?:\.[0-9]+){0,3}(?:[-+][A-Za-z0-9][A-Za-z0-9.-]*)?$")
_PATH = re.compile(
    r"^[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\])?"
    r"(?:\.[A-Za-z_][A-Za-z0-9_]*(?:\[[0-9]+\])?)+$"
)


def _require_text(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise KernelValidationError(f"{label} must be a non-empty string")
    if value != value.strip():
        raise KernelValidationError(f"{label} must not contain leading or trailing whitespace")
    return value


@dataclass(frozen=True, order=True)
class ModelId:
    """Unversioned model identity, e.g. ``component.reaction_wheel``."""

    value: str

    def __post_init__(self) -> None:
        value = _require_text(self.value, "model_id")
        if not _QUALIFIED_ID.fullmatch(value):
            raise KernelValidationError(f"invalid model_id: {value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class ModelRef:
    """Versioned model reference."""

    model_id: ModelId
    version: str

    def __post_init__(self) -> None:
        version = _require_text(self.version, "model version")
        if not _VERSION.fullmatch(version):
            raise KernelValidationError(f"invalid model version: {version!r}")

    @classmethod
    def parse(cls, value: str) -> "ModelRef":
        text = _require_text(value, "model_ref")
        model_id, sep, version = text.rpartition("@")
        if not sep or not model_id or not version:
            raise KernelValidationError(f"model_ref must use '<model_id>@<version>': {text!r}")
        return cls(ModelId(model_id), version)

    def __str__(self) -> str:
        return f"{self.model_id}@{self.version}"


@dataclass(frozen=True, order=True)
class ImplementationId:
    """Versioned implementation identity independent of a Python class path."""

    value: str
    version: str

    def __post_init__(self) -> None:
        value = _require_text(self.value, "implementation_id")
        if not _QUALIFIED_ID.fullmatch(value):
            raise KernelValidationError(f"invalid implementation_id: {value!r}")
        version = _require_text(self.version, "implementation version")
        if not _VERSION.fullmatch(version):
            raise KernelValidationError(f"invalid implementation version: {version!r}")

    @classmethod
    def parse(cls, value: str) -> "ImplementationId":
        text = _require_text(value, "implementation_ref")
        identity, sep, version = text.rpartition("@")
        if not sep or not identity or not version:
            raise KernelValidationError(
                f"implementation_ref must use '<implementation_id>@<version>': {text!r}"
            )
        return cls(identity, version)

    def __str__(self) -> str:
        return f"{self.value}@{self.version}"


@dataclass(frozen=True, order=True)
class LocalId:
    """Identifier local to one definition or graph."""

    value: str

    def __post_init__(self) -> None:
        value = _require_text(self.value, "local_id")
        if not _LOCAL_ID.fullmatch(value):
            raise KernelValidationError(f"invalid local_id: {value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class ObjectRef:
    """Reference to an operations-object property, action, or state."""

    value: str

    def __post_init__(self) -> None:
        value = _require_text(self.value, "object_ref")
        if not _PATH.fullmatch(value):
            raise KernelValidationError(f"invalid object_ref: {value!r}")

    def __str__(self) -> str:
        return self.value


@dataclass(frozen=True, order=True)
class ModelPath:
    """Reference to a node, state, port, or evidence path inside a model graph."""

    value: str

    def __post_init__(self) -> None:
        value = _require_text(self.value, "model_path")
        if not _PATH.fullmatch(value):
            raise KernelValidationError(f"invalid model_path: {value!r}")

    def __str__(self) -> str:
        return self.value


__all__ = [
    "ImplementationId",
    "LocalId",
    "ModelId",
    "ModelPath",
    "ModelRef",
    "ObjectRef",
]
