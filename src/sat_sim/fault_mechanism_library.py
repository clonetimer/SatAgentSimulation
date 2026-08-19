"""Fault mechanism contracts linking causes, physical propagation and observables.

The mechanism library does not claim that a fault is simulation-ready.  It is a
cross-project semantic contract used to align simulation experiments, telemetry
features, AstroGraph knowledge nodes and diagnostic pipelines.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import yaml

FAULT_MECHANISM_SCHEMA_VERSION = "sat-sim.fault-mechanism.v1"
_ALLOWED_STATUS = {"active_provisional", "blocked_pending_basilisk_fault_migration"}
_ALLOWED_ROLES = {"observable_telemetry", "operational_estimate", "simulator_truth"}


@dataclass(frozen=True)
class FaultMechanism:
    mechanism_id: str
    fault_id: str
    subsystem: str
    component: str
    status: str
    feature_contract_id: str
    pipeline_id: str
    diagnostic_signature_id: str | None
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)


def _validate(payload: Mapping[str, Any]) -> FaultMechanism:
    if payload.get("schema_version") != FAULT_MECHANISM_SCHEMA_VERSION:
        raise ValueError(f"unsupported fault mechanism schema: {payload.get('schema_version')!r}")
    required = (
        "mechanism_id", "fault_id", "subsystem", "component", "status", "causes",
        "physical_effects", "propagation_chain", "observables", "feature_contract_id",
        "pipeline_id", "knowledge_graph_projection", "claim_boundary",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"fault mechanism missing fields: {missing}")
    status = str(payload["status"])
    if status not in _ALLOWED_STATUS:
        raise ValueError(f"unsupported fault mechanism status: {status!r}")
    observables = payload.get("observables")
    if not isinstance(observables, list) or not observables:
        raise ValueError("observables must be a non-empty list")
    for index, observable in enumerate(observables):
        if not isinstance(observable, Mapping):
            raise ValueError(f"observable {index} must be an object")
        role = str(observable.get("evidence_role") or "")
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"observable {index} has unsupported evidence_role {role!r}")
        eligible = bool(observable.get("model_input_eligible"))
        if role == "simulator_truth" and eligible:
            raise ValueError("simulator_truth observables cannot be model-input eligible")
    signature = payload.get("diagnostic_signature_id")
    if status == "active_provisional" and not signature:
        raise ValueError("active_provisional mechanism requires diagnostic_signature_id")
    return FaultMechanism(
        mechanism_id=str(payload["mechanism_id"]),
        fault_id=str(payload["fault_id"]),
        subsystem=str(payload["subsystem"]),
        component=str(payload["component"]),
        status=status,
        feature_contract_id=str(payload["feature_contract_id"]),
        pipeline_id=str(payload["pipeline_id"]),
        diagnostic_signature_id=str(signature) if signature else None,
        raw=copy.deepcopy(dict(payload)),
    )


def _iter_assets():
    root = resources.files("sat_sim.fault_mechanisms")
    for domain in sorted(item for item in root.iterdir() if item.is_dir()):
        for item in sorted(domain.iterdir(), key=lambda value: value.name):
            if item.name.endswith((".yaml", ".yml")):
                yield item


def list_fault_mechanisms() -> list[FaultMechanism]:
    items: list[FaultMechanism] = []
    for asset in _iter_assets():
        payload = yaml.safe_load(asset.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"fault mechanism asset must contain an object: {asset.name}")
        items.append(_validate(payload))
    ids = [item.fault_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("fault mechanism library contains duplicate fault_id values")
    return items


def get_fault_mechanism(fault_id: str) -> FaultMechanism:
    matches = [item for item in list_fault_mechanisms() if item.fault_id == fault_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one fault mechanism for {fault_id!r}, found {len(matches)}")
    return matches[0]


def load_fault_mechanism(path: str | Path) -> FaultMechanism:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"fault mechanism must contain an object: {path}")
    return _validate(payload)


__all__ = [
    "FAULT_MECHANISM_SCHEMA_VERSION", "FaultMechanism", "get_fault_mechanism",
    "list_fault_mechanisms", "load_fault_mechanism",
]
