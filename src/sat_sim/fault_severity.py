"""Governed fault-severity semantics for simulation datasets.

A normalized number is not sufficient evidence of physical severity.  This
module binds the normalized value to the simulator parameter that actually
changes, and explicitly represents binary faults that have no continuous
severity axis in the current model.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any, Mapping

FAULT_SEVERITY_MODEL_SCHEMA_VERSION = "sat-sim.fault-severity-model.v1"
FAULT_SEVERITY_DESCRIPTOR_SCHEMA_VERSION = "sat-sim.fault-severity-descriptor.v1"


@dataclass(frozen=True)
class FaultSeverityDescriptor:
    fault_id: str
    normalized_severity: float
    severity_level: str
    mechanism_kind: str
    physically_applied: bool
    simulator_parameter: str | None
    simulator_value: float | int | bool | None
    model: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": FAULT_SEVERITY_DESCRIPTOR_SCHEMA_VERSION,
            "fault_id": self.fault_id,
            "normalized_severity": self.normalized_severity,
            "severity_level": self.severity_level,
            "mechanism_kind": self.mechanism_kind,
            "physically_applied": self.physically_applied,
            "simulator_parameter": self.simulator_parameter,
            "simulator_value": self.simulator_value,
            "model": copy.deepcopy(self.model),
            "claim_boundary": {
                "severity_is_flight_calibrated": False,
                "severity_is_valid_only_for_declared_simulator_parameter": True,
            },
        }


def validate_severity_model(payload: Mapping[str, Any]) -> dict[str, Any]:
    model = copy.deepcopy(dict(payload))
    if model.get("schema_version") != FAULT_SEVERITY_MODEL_SCHEMA_VERSION:
        raise ValueError(f"unsupported severity model schema: {model.get('schema_version')!r}")
    kind = str(model.get("mechanism_kind") or "")
    if kind not in {"continuous_degradation", "binary_failure"}:
        raise ValueError(f"unsupported severity mechanism_kind: {kind!r}")
    bands = model.get("bands")
    if not isinstance(bands, list) or not bands:
        raise ValueError("severity model bands must be a non-empty list")
    previous = -1.0
    for index, band in enumerate(bands):
        if not isinstance(band, Mapping):
            raise ValueError(f"severity band {index} must be an object")
        low = float(band.get("min_inclusive"))
        high = float(band.get("max_inclusive"))
        if not 0.0 <= low <= high <= 1.0:
            raise ValueError(f"invalid severity band range at {index}")
        if low < previous:
            raise ValueError("severity bands must be ordered and non-overlapping")
        previous = high
        if not str(band.get("level") or ""):
            raise ValueError(f"severity band {index} requires level")
    if kind == "binary_failure":
        fixed = float(model.get("fixed_normalized_severity", 1.0))
        if fixed != 1.0:
            raise ValueError("binary failures must use fixed_normalized_severity=1.0")
    else:
        parameter = model.get("physical_parameter")
        if not isinstance(parameter, Mapping) or not str(parameter.get("name") or ""):
            raise ValueError("continuous degradation severity requires physical_parameter.name")
    return model


def _level_for(model: Mapping[str, Any], normalized: float) -> str:
    for band in model.get("bands", []):
        low = float(band.get("min_inclusive"))
        high = float(band.get("max_inclusive"))
        if low <= normalized <= high:
            return str(band.get("level"))
    raise ValueError(f"normalized severity {normalized} does not fall in a governed band")


def build_fault_severity_descriptor(
    *,
    fault_id: str,
    severity_model: Mapping[str, Any],
    normalized_severity: float,
    event_parameters: Mapping[str, Any],
) -> FaultSeverityDescriptor:
    model = validate_severity_model(severity_model)
    kind = str(model["mechanism_kind"])
    normalized = float(normalized_severity)
    if kind == "binary_failure":
        normalized = float(model.get("fixed_normalized_severity", 1.0))
        parameter_name = None
        parameter_value = None
        applied = True
    else:
        if not 0.0 <= normalized <= 1.0:
            raise ValueError("normalized severity must be within [0, 1]")
        parameter_name = str((model.get("physical_parameter") or {}).get("name") or "")
        parameter_value = event_parameters.get(parameter_name)
        applied = parameter_value is not None
    return FaultSeverityDescriptor(
        fault_id=fault_id,
        normalized_severity=normalized,
        severity_level=_level_for(model, normalized),
        mechanism_kind=kind,
        physically_applied=applied,
        simulator_parameter=parameter_name,
        simulator_value=parameter_value,
        model=model,
    )


__all__ = [
    "FAULT_SEVERITY_DESCRIPTOR_SCHEMA_VERSION",
    "FAULT_SEVERITY_MODEL_SCHEMA_VERSION",
    "FaultSeverityDescriptor",
    "build_fault_severity_descriptor",
    "validate_severity_model",
]
