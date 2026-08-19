"""Cross-project diagnostic mapping bundle for AstroGraph integration."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .diagnostic_pipeline_registry import get_diagnostic_pipeline, list_diagnostic_pipelines
from .diagnostic_signature_library import list_diagnostic_signatures
from .fault_mechanism_library import get_fault_mechanism, list_fault_mechanisms
from .telemetry_feature_contract_library import get_telemetry_feature_contract, list_telemetry_feature_contracts

DIAGNOSTIC_MAPPING_BUNDLE_SCHEMA_VERSION = "sat-sim.astrograph-diagnostic-mapping-bundle.v1"


def _sha(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_astrograph_diagnostic_mapping_bundle() -> dict[str, Any]:
    mechanisms = {item.fault_id: item for item in list_fault_mechanisms()}
    features = {item.fault_id: item for item in list_telemetry_feature_contracts()}
    pipelines = {item.fault_id: item for item in list_diagnostic_pipelines()}
    signatures = {item.fault_id: item for item in list_diagnostic_signatures()}
    fault_ids = sorted(set(mechanisms) | set(features) | set(pipelines))
    if set(mechanisms) != set(features) or set(mechanisms) != set(pipelines):
        raise ValueError("mechanism, feature and pipeline libraries must cover identical fault IDs")
    mappings: list[dict[str, Any]] = []
    for fault_id in fault_ids:
        mechanism = mechanisms[fault_id]
        feature = features[fault_id]
        pipeline = pipelines[fault_id]
        if mechanism.feature_contract_id != feature.contract_id:
            raise ValueError(f"feature contract link mismatch for {fault_id}")
        if mechanism.pipeline_id != pipeline.pipeline_id:
            raise ValueError(f"pipeline link mismatch for {fault_id}")
        if feature.mechanism_id != mechanism.mechanism_id or pipeline.mechanism_id != mechanism.mechanism_id:
            raise ValueError(f"mechanism link mismatch for {fault_id}")
        if pipeline.feature_contract_id != feature.contract_id:
            raise ValueError(f"pipeline feature link mismatch for {fault_id}")
        signature = signatures.get(fault_id)
        expected_signature_id = signature.signature_id if signature else None
        if mechanism.diagnostic_signature_id != expected_signature_id:
            raise ValueError(f"mechanism signature link mismatch for {fault_id}")
        if pipeline.diagnostic_signature_id != expected_signature_id:
            raise ValueError(f"pipeline signature link mismatch for {fault_id}")
        raw_features = feature.raw.get("raw_features") or []
        model_inputs = [item["channel"] for item in raw_features if item.get("model_input_eligible")]
        missing_required_model_channels = [
            item["channel"] for item in raw_features
            if item.get("required_for_formal_model_input") and item.get("availability") != "available"
        ]
        truth_inputs = [item["channel"] for item in raw_features if item.get("model_input_eligible") and item.get("evidence_role") == "simulator_truth"]
        if truth_inputs:
            raise ValueError(f"target leakage detected for {fault_id}: {truth_inputs}")
        mapping = {
            "fault_id": fault_id,
            "status": mechanism.status,
            "mechanism": mechanism.to_dict(),
            "feature_contract": feature.to_dict(),
            "pipeline": pipeline.to_dict(),
            "diagnostic_signature": signature.to_dict() if signature else None,
            "astrograph_model_input_channels": model_inputs,
            "missing_required_model_channels": missing_required_model_channels,
            "astrograph_interface_ready": not missing_required_model_channels,
            "formal_model_binding_enabled": (pipeline.raw.get("model_policy") or {}).get("binding_status") == "enabled",
            "pipeline_runtime_supported": pipeline.status == "active_provisional",
            "local_runtime_stage_kinds": [
                item.get("kind") for item in pipeline.raw.get("stages", [])
                if item.get("kind") in {"data_quality_gate", "signature_detector", "feature_extractor", "simulation_evidence"}
            ],
            "external_runtime_stage_kinds": [
                item.get("kind") for item in pipeline.raw.get("stages", [])
                if item.get("kind") in {"ml_model", "kg_reasoner", "fusion", "expert_review_gate"}
            ],
        }
        mapping["mapping_sha256"] = _sha(mapping)
        mappings.append(mapping)
    payload = {
        "schema_version": DIAGNOSTIC_MAPPING_BUNDLE_SCHEMA_VERSION,
        "mapping_count": len(mappings),
        "fault_ids": fault_ids,
        "mappings": mappings,
        "runtime_contract": {
            "schema_version": "sat-sim.astrograph-pipeline-runtime-contract.v1",
            "registry_endpoint": "/diagnostics/pipelines",
            "pipeline_endpoint": "/diagnostics/pipelines/{fault_id}",
            "execute_endpoint": "/diagnostics/pipelines/{fault_id}/execute",
            "runtime_result_schema": "sat-sim.diagnostic-pipeline-runtime.v1",
            "evidence_graph_schema": "sat-sim.simulation-evidence-graph.v1",
            "local_execution_scope": ["data_quality_gate", "signature_detector", "feature_extractor", "simulation_evidence"],
            "astrograph_handoff_scope": ["ml_model", "kg_reasoner", "fusion"],
            "human_governance_scope": ["expert_review_gate"],
            "arbitrary_implementation_import_forbidden": True,
        },
        "governance": {
            "basilisk_required_for_formal_training": True,
            "simulator_truth_model_input_forbidden": True,
            "model_binding_requires_independent_benchmark": True,
            "expert_review_required": True,
        },
    }
    payload["bundle_sha256"] = _sha(payload)
    return payload


def get_astrograph_diagnostic_mapping(fault_id: str) -> dict[str, Any]:
    get_fault_mechanism(fault_id)
    get_telemetry_feature_contract(fault_id)
    get_diagnostic_pipeline(fault_id)
    bundle = build_astrograph_diagnostic_mapping_bundle()
    return next(item for item in bundle["mappings"] if item["fault_id"] == fault_id)


def write_astrograph_diagnostic_mapping_bundle(path: str | Path) -> dict[str, Any]:
    payload = build_astrograph_diagnostic_mapping_bundle()
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload


__all__ = [
    "DIAGNOSTIC_MAPPING_BUNDLE_SCHEMA_VERSION", "build_astrograph_diagnostic_mapping_bundle",
    "get_astrograph_diagnostic_mapping", "write_astrograph_diagnostic_mapping_bundle",
]
