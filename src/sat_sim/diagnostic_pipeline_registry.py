"""Fault-specific diagnostic pipeline registry.

A pipeline is a governed composition contract, not an assertion that a trained
model exists.  Model binding remains disabled until qualified Basilisk data and
independent benchmark evidence are available.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
from importlib import resources
from pathlib import Path
from typing import Any, Mapping

import yaml

DIAGNOSTIC_PIPELINE_SCHEMA_VERSION = "sat-sim.diagnostic-pipeline.v1"
_ALLOWED_STATUS = {"active_provisional", "blocked_pending_basilisk_fault_migration"}
_ALLOWED_STAGE_KINDS = {
    "data_quality_gate", "signature_detector", "feature_extractor", "ml_model",
    "kg_reasoner", "simulation_evidence", "fusion", "expert_review_gate",
}


@dataclass(frozen=True)
class DiagnosticPipeline:
    pipeline_id: str
    fault_id: str
    mechanism_id: str
    feature_contract_id: str
    diagnostic_signature_id: str | None
    status: str
    raw: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return copy.deepcopy(self.raw)


def _validate(payload: Mapping[str, Any]) -> DiagnosticPipeline:
    if payload.get("schema_version") != DIAGNOSTIC_PIPELINE_SCHEMA_VERSION:
        raise ValueError(f"unsupported diagnostic pipeline schema: {payload.get('schema_version')!r}")
    required = (
        "pipeline_id", "fault_id", "mechanism_id", "feature_contract_id", "status",
        "stages", "model_policy", "expert_review_required", "claim_boundary",
    )
    missing = [name for name in required if name not in payload]
    if missing:
        raise ValueError(f"diagnostic pipeline missing fields: {missing}")
    status = str(payload["status"])
    if status not in _ALLOWED_STATUS:
        raise ValueError(f"unsupported diagnostic pipeline status: {status!r}")
    stages = payload.get("stages")
    if not isinstance(stages, list) or not stages:
        raise ValueError("stages must be a non-empty list")
    stage_ids: set[str] = set()
    for index, stage in enumerate(stages):
        if not isinstance(stage, Mapping):
            raise ValueError(f"stage {index} must be an object")
        stage_id = str(stage.get("stage_id") or "")
        if not stage_id or stage_id in stage_ids:
            raise ValueError(f"stage {index} has missing or duplicate stage_id")
        stage_ids.add(stage_id)
        if str(stage.get("kind") or "") not in _ALLOWED_STAGE_KINDS:
            raise ValueError(f"stage {index} uses unsupported kind {stage.get('kind')!r}")
    policy = payload.get("model_policy")
    if not isinstance(policy, Mapping):
        raise ValueError("model_policy must be an object")
    binding_status = str(policy.get("binding_status") or "")
    if binding_status not in {"disabled_pending_benchmark", "blocked_by_simulation_capability", "enabled"}:
        raise ValueError(f"unsupported binding_status: {binding_status!r}")
    if status != "active_provisional" and binding_status == "enabled":
        raise ValueError("blocked pipeline cannot enable model binding")
    signature = payload.get("diagnostic_signature_id")
    if status == "active_provisional" and not signature:
        raise ValueError("active_provisional pipeline requires diagnostic_signature_id")
    return DiagnosticPipeline(
        pipeline_id=str(payload["pipeline_id"]),
        fault_id=str(payload["fault_id"]),
        mechanism_id=str(payload["mechanism_id"]),
        feature_contract_id=str(payload["feature_contract_id"]),
        diagnostic_signature_id=str(signature) if signature else None,
        status=status,
        raw=copy.deepcopy(dict(payload)),
    )


def _iter_assets():
    root = resources.files("sat_sim.diagnostic_pipelines")
    for domain in sorted(item for item in root.iterdir() if item.is_dir()):
        for item in sorted(domain.iterdir(), key=lambda value: value.name):
            if item.name.endswith((".yaml", ".yml")):
                yield item


def list_diagnostic_pipelines() -> list[DiagnosticPipeline]:
    items: list[DiagnosticPipeline] = []
    for asset in _iter_assets():
        payload = yaml.safe_load(asset.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise ValueError(f"diagnostic pipeline asset must contain an object: {asset.name}")
        items.append(_validate(payload))
    ids = [item.fault_id for item in items]
    if len(ids) != len(set(ids)):
        raise ValueError("diagnostic pipeline registry contains duplicate fault_id values")
    return items


def get_diagnostic_pipeline(fault_id: str) -> DiagnosticPipeline:
    matches = [item for item in list_diagnostic_pipelines() if item.fault_id == fault_id]
    if len(matches) != 1:
        raise KeyError(f"expected exactly one diagnostic pipeline for {fault_id!r}, found {len(matches)}")
    return matches[0]


def load_diagnostic_pipeline(path: str | Path) -> DiagnosticPipeline:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, Mapping):
        raise ValueError(f"diagnostic pipeline must contain an object: {path}")
    return _validate(payload)


__all__ = [
    "DIAGNOSTIC_PIPELINE_SCHEMA_VERSION", "DiagnosticPipeline", "get_diagnostic_pipeline",
    "list_diagnostic_pipelines", "load_diagnostic_pipeline",
]
