"""Governed runtime for fault-specific diagnostic pipeline contracts.

Only registered deterministic local handlers execute in this process. AstroGraph
model, Neo4j, fusion and expert stages are represented as structured handoffs;
no arbitrary implementation reference is imported or executed.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .diagnostic_feature_extractor import extract_pair_features
from .diagnostic_mapping_bundle import get_astrograph_diagnostic_mapping
from .diagnostic_pipeline_registry import DiagnosticPipeline, get_diagnostic_pipeline
from .diagnostic_validation_agent import DiagnosticValidationAgent
from .fault_mechanism_library import get_fault_mechanism
from .simulation_evidence_graph import build_simulation_evidence_graph
from .simulation_validation_agent import SimulationValidationAgent

DIAGNOSTIC_PIPELINE_RUNTIME_SCHEMA_VERSION = "sat-sim.diagnostic-pipeline-runtime.v1"
_LOCAL_KINDS = {"data_quality_gate", "signature_detector", "feature_extractor", "simulation_evidence"}
_EXTERNAL_KINDS = {"ml_model", "kg_reasoner", "fusion", "expert_review_gate"}


def _sha(payload: Mapping[str, Any]) -> str:
    raw = json.dumps(dict(payload), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(dict(payload), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _external_result(stage_id: str, provided: Mapping[str, Any]) -> dict[str, Any] | None:
    value = provided.get(stage_id)
    if not isinstance(value, Mapping):
        return None
    return copy.deepcopy(dict(value))


class DiagnosticPipelineExecutor:
    """Execute local pipeline gates and emit an AstroGraph handoff package."""

    def execute(
        self,
        *,
        fault_root: str | Path,
        nominal_root: str | Path,
        fault_id: str | None = None,
        external_stage_outputs: Mapping[str, Any] | None = None,
        persist: bool = True,
    ) -> dict[str, Any]:
        fault_root = Path(fault_root)
        nominal_root = Path(nominal_root)
        fault_contract_path = fault_root / "astrograph" / "dataset_contract.json"
        if not fault_contract_path.is_file():
            raise FileNotFoundError(fault_contract_path)
        fault_contract = json.loads(fault_contract_path.read_text(encoding="utf-8"))
        declared_fault_id = str((fault_contract.get("ground_truth") or {}).get("class_id") or "")
        if fault_id and fault_id != declared_fault_id:
            raise ValueError(f"requested fault_id {fault_id!r} does not match dataset {declared_fault_id!r}")
        fault_id = fault_id or declared_fault_id
        pipeline = get_diagnostic_pipeline(fault_id)
        mechanism = get_fault_mechanism(fault_id)
        mapping = get_astrograph_diagnostic_mapping(fault_id)
        external = dict(external_stage_outputs or {})
        stage_results: list[dict[str, Any]] = []
        outputs: dict[str, Any] = {}
        physics_report: dict[str, Any] | None = None
        diagnostic_report: dict[str, Any] | None = None
        feature_report: dict[str, Any] | None = None
        graph: dict[str, Any] | None = None
        local_rejected = False

        if pipeline.status != "active_provisional":
            result = {
                "schema_version": DIAGNOSTIC_PIPELINE_RUNTIME_SCHEMA_VERSION,
                "pipeline_id": pipeline.pipeline_id,
                "fault_id": fault_id,
                "execution_status": "BLOCKED_PIPELINE_STATUS",
                "reason": pipeline.status,
                "stage_results": [],
                "formal_diagnosis_publishable": False,
                "training_ready": False,
                "claim_boundary": {"configuration_is_not_executable_capability": True},
            }
            result["runtime_sha256"] = _sha(result)
            return result

        for stage in pipeline.raw.get("stages") or []:
            stage_id = str(stage["stage_id"])
            kind = str(stage["kind"])
            base = {
                "stage_id": stage_id,
                "kind": kind,
                "implementation_ref": stage.get("implementation_ref"),
                "required": bool(stage.get("required")),
                "output_ref": stage.get("output_ref"),
            }
            if local_rejected and kind in _EXTERNAL_KINDS:
                stage_results.append({**base, "status": "BLOCKED_UPSTREAM", "reason": "local_required_gate_rejected"})
                continue
            if kind == "data_quality_gate":
                physics_report = SimulationValidationAgent().validate_pair(fault_root=fault_root, nominal_root=nominal_root)
                status = "COMPLETED" if physics_report.get("decision") == "PASS" else "COMPLETED_REVIEW" if physics_report.get("decision") == "REVIEW" else "REJECTED"
                stage_results.append({**base, "status": status, "decision": physics_report.get("decision"), "reason": physics_report.get("reason")})
                outputs[str(stage.get("output_ref"))] = physics_report
                local_rejected = physics_report.get("decision") == "REJECT"
            elif kind == "signature_detector":
                diagnostic_report = DiagnosticValidationAgent().validate_pair(fault_root=fault_root, nominal_root=nominal_root)
                status = "COMPLETED" if diagnostic_report.get("decision") == "PASS" else "COMPLETED_REVIEW" if diagnostic_report.get("decision") == "REVIEW" else "REJECTED"
                stage_results.append({**base, "status": status, "decision": diagnostic_report.get("decision"), "diagnostic_score": diagnostic_report.get("diagnostic_score"), "reason": diagnostic_report.get("reason")})
                outputs[str(stage.get("output_ref"))] = diagnostic_report
                local_rejected = local_rejected or diagnostic_report.get("decision") == "REJECT"
            elif kind == "feature_extractor":
                feature_report = extract_pair_features(fault_root=fault_root, nominal_root=nominal_root)
                status = "COMPLETED" if feature_report.get("status") == "COMPLETED" else "COMPLETED_PARTIAL"
                stage_results.append({**base, "status": status, "feature_count": len(feature_report.get("feature_vector") or {}), "missing_required_model_channels": feature_report.get("missing_required_model_channels"), "blocked_derived_features": feature_report.get("blocked_derived_features")})
                outputs[str(stage.get("output_ref"))] = feature_report
            elif kind == "simulation_evidence":
                if physics_report is None or diagnostic_report is None or feature_report is None:
                    stage_results.append({**base, "status": "BLOCKED_UPSTREAM", "reason": "local_evidence_not_available"})
                    local_rejected = True
                else:
                    graph = build_simulation_evidence_graph(
                        fault_root=fault_root,
                        nominal_root=nominal_root,
                        mechanism=mechanism,
                        pipeline=pipeline,
                        physics_report=physics_report,
                        diagnostic_report=diagnostic_report,
                        feature_report=feature_report,
                        stage_results=stage_results,
                    )
                    stage_results.append({**base, "status": "COMPLETED", "graph_id": graph.get("graph_id"), "graph_sha256": graph.get("graph_sha256")})
                    outputs[str(stage.get("output_ref"))] = graph
            elif kind in _EXTERNAL_KINDS:
                provided = _external_result(stage_id, external)
                if provided is not None:
                    stage_results.append({**base, "status": "COMPLETED_EXTERNAL", "provider_assertion": provided.get("status") or provided.get("decision"), "output_sha256": _sha(provided)})
                    outputs[str(stage.get("output_ref"))] = provided
                elif kind == "ml_model" and not bool(stage.get("required")):
                    stage_results.append({**base, "status": "SKIPPED_OPTIONAL", "reason": "formal_model_binding_disabled_pending_benchmark"})
                else:
                    stage_results.append({**base, "status": "PENDING_ASTROGRAPH" if kind != "expert_review_gate" else "PENDING_EXPERT", "reason": "external_governed_stage_not_supplied"})
            else:
                raise ValueError(f"no registered runtime handler for stage kind {kind!r}")

        required_pending = [item["stage_id"] for item in stage_results if item["required"] and item["status"] in {"PENDING_ASTROGRAPH", "PENDING_EXPERT", "BLOCKED_UPSTREAM"}]
        required_rejected = [item["stage_id"] for item in stage_results if item["required"] and item["status"] == "REJECTED"]
        interface_missing = list(mapping.get("missing_required_model_channels") or [])
        local_review = any(item["status"] in {"COMPLETED_REVIEW", "COMPLETED_PARTIAL"} for item in stage_results)
        if required_rejected:
            execution_status = "REJECTED_LOCAL_EVIDENCE"
        elif interface_missing:
            execution_status = "LOCAL_RUNTIME_COMPLETE_INTERFACE_BLOCKED"
        elif required_pending:
            execution_status = "READY_FOR_ASTROGRAPH_REVIEW" if local_review else "READY_FOR_ASTROGRAPH_HANDOFF"
        else:
            execution_status = "COMPLETE_REVIEW_REQUIRED" if local_review else "COMPLETE"

        handoff_requests = [
            {
                "stage_id": item["stage_id"],
                "kind": item["kind"],
                "implementation_ref": item["implementation_ref"],
                "required": item["required"],
                "expected_output_ref": item["output_ref"],
            }
            for item in stage_results
            if item["status"] in {"PENDING_ASTROGRAPH", "PENDING_EXPERT"}
        ]
        result: dict[str, Any] = {
            "schema_version": DIAGNOSTIC_PIPELINE_RUNTIME_SCHEMA_VERSION,
            "runtime_engine": "governed-diagnostic-pipeline-executor.v1",
            "pipeline_id": pipeline.pipeline_id,
            "fault_id": fault_id,
            "pair_id": (fault_contract.get("ground_truth") or {}).get("pair_id"),
            "fault_dataset_id": fault_contract.get("dataset_id"),
            "execution_status": execution_status,
            "stage_results": stage_results,
            "local_outputs": {
                "physics_validation": physics_report,
                "diagnostic_validation": diagnostic_report,
                "feature_vector": feature_report,
                "simulation_evidence_graph": graph,
            },
            "astrograph_handoff": {
                "status": "BLOCKED_REQUIRED_CHANNELS" if interface_missing else "READY" if handoff_requests else "NOT_REQUIRED",
                "missing_required_model_channels": interface_missing,
                "requests": handoff_requests,
                "external_stage_execution_is_data_only": True,
                "arbitrary_implementation_import_forbidden": True,
            },
            "formal_diagnosis_publishable": False,
            "formal_model_binding_enabled": bool(mapping.get("formal_model_binding_enabled")),
            "training_ready": False,
            "claim_boundary": {
                "local_runtime_does_not_execute_astrograph_models_or_neo4j": True,
                "pipeline_completion_is_not_accuracy_evidence": True,
                "named_expert_review_required": True,
                "basilisk_a_level_required_for_formal_claims": True,
                "astrograph_is_authority_for_final_diagnosis_publication": True,
            },
        }
        result["runtime_sha256"] = _sha(result)
        if persist:
            astrograph_dir = fault_root / "astrograph"
            _write_json(astrograph_dir / "diagnostic_pipeline_runtime.json", result)
            if graph is not None:
                _write_json(astrograph_dir / "simulation_evidence_graph.json", graph)
        return result


__all__ = ["DIAGNOSTIC_PIPELINE_RUNTIME_SCHEMA_VERSION", "DiagnosticPipelineExecutor"]
