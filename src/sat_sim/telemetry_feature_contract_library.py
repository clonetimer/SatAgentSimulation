"""Telemetry feature contracts shared by simulation and AstroGraph."""
from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import yaml

TELEMETRY_FEATURE_CONTRACT_SCHEMA_VERSION = "sat-sim.telemetry-feature-contract.v1"
_ALLOWED_STATUS = {"active_provisional", "blocked_pending_basilisk_fault_migration"}
_ALLOWED_ROLES = {"observable_telemetry", "operational_estimate", "simulator_truth"}


@dataclass(frozen=True)
class TelemetryFeatureContract:
    contract_id: str
    fault_id: str
    mechanism_id: str
    status: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)


def _validate(payload: Mapping[str, Any]) -> TelemetryFeatureContract:
    if payload.get("schema_version") != TELEMETRY_FEATURE_CONTRACT_SCHEMA_VERSION:
        raise ValueError(f"unsupported telemetry feature contract schema: {payload.get('schema_version')!r}")
    required = (
        "contract_id", "fault_id", "mechanism_id", "status", "telemetry_profile",
        "raw_features", "derived_features", "label_policy", "split_policy",
        "missing_data_policy", "claim_boundary",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"telemetry feature contract missing fields: {missing}")
    status = str(payload["status"])
    if status not in _ALLOWED_STATUS:
        raise ValueError(f"unsupported telemetry feature contract status: {status!r}")
    raw_features = payload.get("raw_features")
    if not isinstance(raw_features, list) or not raw_features:
        raise ValueError("raw_features must be a non-empty list")
    feature_ids: set[str] = set()
    for index, feature in enumerate(raw_features):
        if not isinstance(feature, Mapping):
            raise ValueError(f"raw feature {index} must be an object")
        feature_id = str(feature.get("feature_id") or "")
        if not feature_id or feature_id in feature_ids:
            raise ValueError(f"raw feature {index} has missing or duplicate feature_id")
        feature_ids.add(feature_id)
        role = str(feature.get("evidence_role") or "")
        if role not in _ALLOWED_ROLES:
            raise ValueError(f"raw feature {index} has unsupported evidence_role {role!r}")
        availability = str(feature.get("availability") or "")
        if availability not in {"available", "missing_from_simulator", "missing_from_astrograph"}:
            raise ValueError(f"raw feature {index} has unsupported availability {availability!r}")
        if role == "simulator_truth" and bool(feature.get("model_input_eligible")):
            raise ValueError("simulator_truth feature cannot be model-input eligible")
        if availability != "available" and bool(feature.get("model_input_eligible")):
            raise ValueError("unavailable raw feature cannot be model-input eligible")
    derived = payload.get("derived_features")
    if not isinstance(derived, list):
        raise ValueError("derived_features must be a list")
    for index, feature in enumerate(derived):
        if not isinstance(feature, Mapping):
            raise ValueError(f"derived feature {index} must be an object")
        feature_id = str(feature.get("feature_id") or "")
        if not feature_id or feature_id in feature_ids:
            raise ValueError(f"derived feature {index} has missing or duplicate feature_id")
        feature_ids.add(feature_id)
        if bool(feature.get("model_input_eligible")) and not bool(feature.get("leakage_safe")):
            raise ValueError("model-input eligible derived feature must be leakage_safe")
    return TelemetryFeatureContract(
        contract_id=str(payload["contract_id"]),
        fault_id=str(payload["fault_id"]),
        mechanism_id=str(payload["mechanism_id"]),
        status=status,
        raw=copy.deepcopy(dict(payload)),
    )


def _iter_assets():
    root = resources.files("sat_sim.telemetry_feature_contracts")
    for domain in sorted(item for item in root.iterdir() if item.is_dir()):
        for item in sorted(domain.iterdir(), key=lambda value: value.name):
            if item.name.endswith((".yaml", ".yml")):
                yield item


def list_telemetry_feature_contracts() -> list[TelemetryFeatureContract]:
    items: list[TelemetryFeatureContract] = []
    for asset in _iter_assets():
        payload = yaml.safe_load(asset.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"telemetry feature contract asset must contain an object: {asset.name}")
        items.append(_validate(payload))
    ids = [item.fault_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("telemetry feature contract library contains duplicate fault_id values")
    return items


def get_telemetry_feature_contract(fault_id: str) -> TelemetryFeatureContract:
    matches = [item for item in list_telemetry_feature_contracts() if item.fault_id == fault_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one telemetry feature contract for {fault_id!r}, found {len(matches)}")
    return matches[0]


def load_telemetry_feature_contract(path: str | Path) -> TelemetryFeatureContract:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"telemetry feature contract must contain an object: {path}")
    return _validate(payload)


__all__ = [
    "TELEMETRY_FEATURE_CONTRACT_SCHEMA_VERSION", "TelemetryFeatureContract",
    "get_telemetry_feature_contract", "list_telemetry_feature_contracts",
    "load_telemetry_feature_contract",
]
