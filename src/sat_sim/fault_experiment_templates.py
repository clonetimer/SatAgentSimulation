"""Governed, package-resident fault experiment templates.

Templates are declarative contracts for reproducible experiments.  They do not
execute Basilisk and they do not replace TaskSpec validation.  The fault dataset
factory embeds the selected template identity and expected-response contract in
each case so that downstream validation is deterministic and auditable.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import yaml

FAULT_EXPERIMENT_TEMPLATE_SCHEMA_VERSION = "sat-sim.fault-experiment-template.v1"


@dataclass(frozen=True)
class FaultExperimentTemplate:
    template_id: str
    fault_id: str
    subsystem: str
    component: str
    capability_id: str
    simulation_backend: str
    event_kind: str
    simulator_effect: str
    expected_response: dict[str, Any]
    qualification: dict[str, Any]
    severity_model: dict[str, Any]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)


def _validate(payload: Mapping[str, Any]) -> FaultExperimentTemplate:
    if payload.get("schema_version") != FAULT_EXPERIMENT_TEMPLATE_SCHEMA_VERSION:
        raise ValueError(f"unsupported fault experiment template schema: {payload.get('schema_version')!r}")
    required = ("template_id", "fault_id", "subsystem", "component", "simulation", "fault", "expected_response", "qualification", "severity_model")
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"fault experiment template missing fields: {missing}")
    simulation = payload.get("simulation")
    fault = payload.get("fault")
    expected = payload.get("expected_response")
    qualification = payload.get("qualification")
    severity_model = payload.get("severity_model")
    if not all(isinstance(item, Mapping) for item in (simulation, fault, expected, qualification, severity_model)):
        raise ValueError("simulation/fault/expected_response/qualification/severity_model must be objects")
    from .fault_severity import validate_severity_model
    validated_severity = validate_severity_model(severity_model)
    capability_id = str(simulation.get("capability_id") or "")
    backend = str(simulation.get("backend") or "")
    if capability_id != "subsystem.adcs_unified_native.v1" or backend != "basilisk":
        raise ValueError("formal fault experiment templates must use unified native ADCS with Basilisk")
    event_kind = str(fault.get("event_kind") or "")
    if event_kind not in {"fault", "degradation"}:
        raise ValueError(f"unsupported event kind: {event_kind!r}")
    signatures = expected.get("signatures")
    if not isinstance(signatures, list) or not signatures:
        raise ValueError("expected_response.signatures must be a non-empty list")
    for index, signature in enumerate(signatures):
        if not isinstance(signature, Mapping):
            raise ValueError(f"signature {index} must be an object")
        for name in ("check_id", "channel", "comparison", "threshold"):
            if name not in signature:
                raise ValueError(f"signature {index} missing {name}")
    return FaultExperimentTemplate(
        template_id=str(payload["template_id"]),
        fault_id=str(payload["fault_id"]),
        subsystem=str(payload["subsystem"]),
        component=str(payload["component"]),
        capability_id=capability_id,
        simulation_backend=backend,
        event_kind=event_kind,
        simulator_effect=str(fault.get("simulator_effect") or ""),
        expected_response=copy.deepcopy(dict(expected)),
        qualification=copy.deepcopy(dict(qualification)),
        severity_model=validated_severity,
        raw=copy.deepcopy(dict(payload)),
    )


def _asset_names() -> tuple[str, ...]:
    root = resources.files("sat_sim.experiment_templates.adcs")
    return tuple(sorted(item.name for item in root.iterdir() if item.name.endswith((".yaml", ".yml"))))


def list_fault_experiment_templates() -> list[FaultExperimentTemplate]:
    root = resources.files("sat_sim.experiment_templates.adcs")
    templates: list[FaultExperimentTemplate] = []
    for name in _asset_names():
        payload = yaml.safe_load(root.joinpath(name).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"template asset must contain an object: {name}")
        templates.append(_validate(payload))
    return templates


def get_fault_experiment_template(fault_id: str) -> FaultExperimentTemplate:
    matches = [item for item in list_fault_experiment_templates() if item.fault_id == fault_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one experiment template for {fault_id!r}, found {len(matches)}")
    return matches[0]


def load_fault_experiment_template(path: str | Path) -> FaultExperimentTemplate:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"template must contain an object: {path}")
    return _validate(payload)


__all__ = [
    "FAULT_EXPERIMENT_TEMPLATE_SCHEMA_VERSION",
    "FaultExperimentTemplate",
    "get_fault_experiment_template",
    "list_fault_experiment_templates",
    "load_fault_experiment_template",
]
