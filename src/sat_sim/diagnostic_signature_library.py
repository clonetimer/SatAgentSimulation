"""Package-resident diagnostic-signature contracts.

Physical validation asks whether a simulator applied the requested mechanism.
Diagnostic validation asks whether the resulting telemetry contains a stable,
auditable signal that a diagnosis system can learn.  Thresholds in the initial
library are provisional until the 18-case Basilisk gate is executed.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import yaml

DIAGNOSTIC_SIGNATURE_SCHEMA_VERSION = "sat-sim.diagnostic-signature.v1"


@dataclass(frozen=True)
class DiagnosticSignature:
    signature_id: str
    fault_id: str
    source_experiment_template_id: str
    required_channels: tuple[str, ...]
    criteria: tuple[dict[str, Any], ...]
    qualification: dict[str, Any]
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)


def _validate(payload: Mapping[str, Any]) -> DiagnosticSignature:
    if payload.get("schema_version") != DIAGNOSTIC_SIGNATURE_SCHEMA_VERSION:
        raise ValueError(f"unsupported diagnostic signature schema: {payload.get('schema_version')!r}")
    required = (
        "signature_id",
        "fault_id",
        "source_experiment_template_id",
        "required_channels",
        "criteria",
        "qualification",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"diagnostic signature missing fields: {missing}")
    channels = payload.get("required_channels")
    criteria = payload.get("criteria")
    qualification = payload.get("qualification")
    if not isinstance(channels, list) or not channels or not all(isinstance(item, str) and item for item in channels):
        raise ValueError("required_channels must be a non-empty string list")
    if not isinstance(criteria, list) or not criteria:
        raise ValueError("criteria must be a non-empty list")
    allowed_metrics = {
        "post_fault_mean_minus_nominal_gt",
        "post_pair_max_abs_delta_gt",
        "post_pair_rms_delta_gt",
        "post_fault_max_le",
        "post_fault_to_nominal_mean_ratio_lt",
        "post_fault_fraction_le_gte",
        "post_pair_threshold_persistence_gte",
        "paired_delta_onset_latency_le",
    }
    for index, criterion in enumerate(criteria):
        if not isinstance(criterion, Mapping):
            raise ValueError(f"criterion {index} must be an object")
        for name in ("criterion_id", "channel", "metric", "threshold", "evidence_role", "contributes_to_diagnostic_score"):
            if name not in criterion:
                raise ValueError(f"criterion {index} missing {name}")
        if str(criterion.get("metric")) not in allowed_metrics:
            raise ValueError(f"criterion {index} uses unsupported metric {criterion.get('metric')!r}")
        role = str(criterion.get("evidence_role") or "")
        if role not in {"observable_telemetry", "operational_estimate", "simulator_truth"}:
            raise ValueError(f"criterion {index} uses unsupported evidence_role {role!r}")
        if role == "simulator_truth" and bool(criterion.get("contributes_to_diagnostic_score")):
            raise ValueError("simulator_truth criteria cannot contribute to diagnostic score")
    if not isinstance(qualification, Mapping):
        raise ValueError("qualification must be an object")
    minimum_score = float(qualification.get("minimum_diagnostic_score", 0.0))
    if int(qualification.get("minimum_observable_criteria_passed", 0)) < 1:
        raise ValueError("minimum_observable_criteria_passed must be at least one")
    if not 0.0 <= minimum_score <= 1.0:
        raise ValueError("minimum_diagnostic_score must be within [0,1]")
    return DiagnosticSignature(
        signature_id=str(payload["signature_id"]),
        fault_id=str(payload["fault_id"]),
        source_experiment_template_id=str(payload["source_experiment_template_id"]),
        required_channels=tuple(channels),
        criteria=tuple(copy.deepcopy(dict(item)) for item in criteria),
        qualification=copy.deepcopy(dict(qualification)),
        raw=copy.deepcopy(dict(payload)),
    )


def _asset_names() -> tuple[str, ...]:
    root = resources.files("sat_sim.diagnostic_signatures.adcs")
    return tuple(sorted(item.name for item in root.iterdir() if item.name.endswith((".yaml", ".yml"))))


def list_diagnostic_signatures() -> list[DiagnosticSignature]:
    root = resources.files("sat_sim.diagnostic_signatures.adcs")
    items: list[DiagnosticSignature] = []
    for name in _asset_names():
        payload = yaml.safe_load(root.joinpath(name).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"diagnostic signature asset must contain an object: {name}")
        items.append(_validate(payload))
    return items


def get_diagnostic_signature(fault_id: str) -> DiagnosticSignature:
    matches = [item for item in list_diagnostic_signatures() if item.fault_id == fault_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one diagnostic signature for {fault_id!r}, found {len(matches)}")
    return matches[0]


def load_diagnostic_signature(path: str | Path) -> DiagnosticSignature:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"diagnostic signature must contain an object: {path}")
    return _validate(payload)


__all__ = [
    "DIAGNOSTIC_SIGNATURE_SCHEMA_VERSION",
    "DiagnosticSignature",
    "get_diagnostic_signature",
    "list_diagnostic_signatures",
    "load_diagnostic_signature",
]
