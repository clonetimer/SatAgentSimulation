"""Shared value types for model definitions, graphs, and bindings."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import re

from .errors import KernelValidationError


class DataType(str, Enum):
    NUMBER = "number"
    INTEGER = "integer"
    BOOLEAN = "boolean"
    STRING = "string"
    VECTOR = "vector"
    ARRAY = "array"
    OBJECT = "object"
    ENUM = "enum"


class ModelKind(str, Enum):
    COMPONENT = "component"
    SUBSYSTEM = "subsystem"
    SPACECRAFT = "spacecraft"
    ENVIRONMENT = "environment"
    SCENARIO = "scenario"


class PortDirection(str, Enum):
    INPUT = "input"
    OUTPUT = "output"


class EffectKind(str, Enum):
    FAULT = "fault"
    DEGRADATION = "degradation"
    CONSTRAINT = "constraint"
    DISTURBANCE = "disturbance"
    MODE = "mode"


class BindingKind(str, Enum):
    PROPERTY = "property"
    ACTION = "action"
    EFFECT = "effect"
    EVIDENCE = "evidence"


class TimeSemantics(str, Enum):
    STATIC = "static"
    CONTINUOUS = "continuous"
    DISCRETE = "discrete"
    EVENT = "event"


class ConnectionKind(str, Enum):
    SIGNAL = "signal"
    RESOURCE = "resource"
    STATE = "state"
    EVENT = "event"


_FRAME = re.compile(r"^[A-Za-z][A-Za-z0-9_.-]*$")


@dataclass(frozen=True, order=True)
class FrameRef:
    """Named reference frame without depending on an engine frame class."""

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str) or not _FRAME.fullmatch(self.value):
            raise KernelValidationError(f"invalid frame reference: {self.value!r}")

    def __str__(self) -> str:
        return self.value


__all__ = [
    "BindingKind",
    "ConnectionKind",
    "DataType",
    "EffectKind",
    "FrameRef",
    "ModelKind",
    "PortDirection",
    "TimeSemantics",
]
