"""Round-trip application of AstroGraph review-only external stage outputs.

The Satellite Simulation platform remains authoritative only for local simulation,
quality, signature, feature and evidence stages.  This helper validates an
AstroGraph Phase 3-G closure result, replays the registered diagnostic pipeline
with data-only external stage outputs, and persists the resulting cross-project
trace.  It never accepts an external assertion of formal publication or training
readiness.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .diagnostic_pipeline_runtime import DiagnosticPipelineExecutor

ASTROGRAPH_CLOSURE_SCHEMA_VERSION = "astrograph-sat-sim-pipeline-closure.v1"
ASTROGRAPH_ROUNDTRIP_SCHEMA_VERSION = "sat-sim.astrograph-roundtrip.v1"
_ALLOWED_EXTERNAL_STAGES = {"ml_model", "kg_reasoner", "fusion"}


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _validate_closure(closure: Mapping[str, Any], *, expected_fault_id: str | None = None) -> dict[str, Any]:
    value = dict(closure)
    if value.get("schema_version") != ASTROGRAPH_CLOSURE_SCHEMA_VERSION:
        raise ValueError("unsupported AstroGraph closure schema")
    if expected_fault_id and value.get("fault_id") != expected_fault_id:
        raise ValueError("AstroGraph closure fault does not match requested fault")
    if value.get("formal_diagnosis_publishable") is not False:
        raise ValueError("Satellite Simulation platform cannot accept formal diagnosis publication")
    if value.get("training_ready") is not False:
        raise ValueError("unqualified cross-project closure cannot assert training readiness")
    external = value.get("external_stage_outputs")
    if not isinstance(external, Mapping):
        raise ValueError("AstroGraph closure does not contain external stage outputs")
    unknown = set(external) - _ALLOWED_EXTERNAL_STAGES
    if unknown:
        raise ValueError(f"unsupported AstroGraph external stages: {sorted(unknown)}")
    if "expert_gate" in external:
        raise ValueError("named expert gate cannot be completed by cross-project payload")
    return value


def apply_astrograph_closure(
    *,
    fault_root: str | Path,
    nominal_root: str | Path,
    closure: Mapping[str, Any],
    persist: bool = True,
) -> dict[str, Any]:
    fault_root = Path(fault_root)
    contract_path = fault_root / "astrograph" / "dataset_contract.json"
    if not contract_path.is_file():
        raise FileNotFoundError(contract_path)
    contract = json.loads(contract_path.read_text(encoding="utf-8"))
    fault_id = str((contract.get("ground_truth") or {}).get("class_id") or "")
    validated = _validate_closure(closure, expected_fault_id=fault_id)
    pipeline_runtime = DiagnosticPipelineExecutor().execute(
        fault_root=fault_root,
        nominal_root=nominal_root,
        fault_id=fault_id,
        external_stage_outputs=validated["external_stage_outputs"],
        persist=persist,
    )
    stage_status = {item["stage_id"]: item["status"] for item in pipeline_runtime.get("stage_results") or []}
    result = {
        "schema_version": ASTROGRAPH_ROUNDTRIP_SCHEMA_VERSION,
        "fault_id": fault_id,
        "pair_id": pipeline_runtime.get("pair_id"),
        "astrograph_closure_id": validated.get("closure_id"),
        "astrograph_closure_sha256": validated.get("closure_sha256") or _sha(validated),
        "pipeline_runtime": pipeline_runtime,
        "completed_external_stages": sorted(
            stage_id for stage_id, status in stage_status.items() if status == "COMPLETED_EXTERNAL"
        ),
        "pending_expert_gate": stage_status.get("expert_gate") == "PENDING_EXPERT",
        "formal_diagnosis_publishable": False,
        "training_ready": False,
        "claim_boundary": {
            "roundtrip_proves_interface_execution_only": True,
            "sat_sim_cannot_publish_astrograph_diagnosis": True,
            "named_expert_review_still_required": True,
            "basilisk_a_level_still_required": True,
        },
    }
    result["roundtrip_sha256"] = _sha(result)
    if persist:
        output = fault_root / "astrograph" / "astrograph_roundtrip.json"
        output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


__all__ = [
    "ASTROGRAPH_CLOSURE_SCHEMA_VERSION",
    "ASTROGRAPH_ROUNDTRIP_SCHEMA_VERSION",
    "apply_astrograph_closure",
]
