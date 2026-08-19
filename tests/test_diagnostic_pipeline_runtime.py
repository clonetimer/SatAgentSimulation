from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from sat_sim.api import create_app
from sat_sim.diagnostic_mapping_bundle import build_astrograph_diagnostic_mapping_bundle
from sat_sim.diagnostic_pipeline_runtime import DiagnosticPipelineExecutor
from sat_sim.fault_dataset_factory import (
    FaultDatasetFactoryConfig,
    SplitCounts,
    build_fault_dataset_campaign,
    run_fault_dataset_campaign,
)

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def proxy_dataset(tmp_path_factory: pytest.TempPathFactory):
    root = tmp_path_factory.mktemp("phase3f-runtime")
    config = FaultDatasetFactoryConfig(
        campaign_id="phase3f_proxy_runtime",
        counts=SplitCounts(train=1, validation=0, test=0),
        duration_s=8.0,
        sample_s=1.0,
        solver_step_s=0.2,
        capability_id="subsystem.adcs_fidelity.v1",
        simulation_backend="python",
        allow_test_proxy=True,
    )
    result = run_fault_dataset_campaign(
        build_fault_dataset_campaign(config), output_root=root / "dataset", continue_on_error=False
    )
    return result


def _pair_roots(result, fault_id: str) -> tuple[Path, Path]:
    report = next(item for item in result.dataset_index["diagnostic_qualification"]["reports"] if item["fault_id"] == fault_id)
    cases = result.campaign.output_root / "cases"
    return cases / report["fault_dataset_id"], cases / report["nominal_dataset_id"]


def test_mapping_bundle_publishes_runtime_contract() -> None:
    bundle = build_astrograph_diagnostic_mapping_bundle()
    jsonschema.validate(bundle, _read(ROOT / "src/sat_sim/schemas/diagnostic_mapping_bundle.schema.json"))
    assert bundle["runtime_contract"]["runtime_result_schema"] == "sat-sim.diagnostic-pipeline-runtime.v1"
    by_fault = {item["fault_id"]: item for item in bundle["mappings"]}
    assert by_fault["ADCS_RW_JAM"]["pipeline_runtime_supported"] is True
    assert by_fault["EPS_BATTERY_CAPACITY_LOSS"]["pipeline_runtime_supported"] is False
    assert "kg_reasoner" in by_fault["ADCS_RW_JAM"]["external_runtime_stage_kinds"]


def test_rw_jam_runtime_emits_feature_and_evidence_artifacts(proxy_dataset) -> None:
    fault_root, nominal_root = _pair_roots(proxy_dataset, "ADCS_RW_JAM")
    result = DiagnosticPipelineExecutor().execute(fault_root=fault_root, nominal_root=nominal_root)
    jsonschema.validate(result, _read(ROOT / "src/sat_sim/schemas/diagnostic_pipeline_runtime.schema.json"))
    feature = result["local_outputs"]["feature_vector"]
    graph = result["local_outputs"]["simulation_evidence_graph"]
    jsonschema.validate(feature, _read(ROOT / "src/sat_sim/schemas/diagnostic_feature_vector.schema.json"))
    jsonschema.validate(graph, _read(ROOT / "src/sat_sim/schemas/simulation_evidence_graph.schema.json"))
    assert result["execution_status"] == "READY_FOR_ASTROGRAPH_REVIEW"
    assert result["formal_diagnosis_publishable"] is False
    assert result["training_ready"] is False
    assert result["astrograph_handoff"]["missing_required_model_channels"] == []
    assert (fault_root / "astrograph/diagnostic_pipeline_runtime.json").is_file()
    assert (fault_root / "astrograph/simulation_evidence_graph.json").is_file()
    assert graph["neo4j_projection"]["status"] == "CANDIDATE_ONLY"
    assert graph["neo4j_projection"]["formal_knowledge_publishable"] is False


def test_feature_extractor_never_emits_truth_or_label_inputs(proxy_dataset) -> None:
    fault_root, nominal_root = _pair_roots(proxy_dataset, "ADCS_RW_JAM")
    result = DiagnosticPipelineExecutor().execute(fault_root=fault_root, nominal_root=nominal_root, persist=False)
    feature = result["local_outputs"]["feature_vector"]
    channels = set(feature["feature_channels"].values())
    assert not any(channel.startswith("label.") for channel in channels)
    assert "status.adcs.rw.available_0" not in channels
    assert "estimate.adcs.rw.effective_max_torque_nm_0" not in channels
    assert feature["leakage_guard"]["emitted_simulator_truth_count"] == 0
    blocked = {item["feature_id"]: item for item in feature["derived_feature_results"]}
    assert blocked["torque_speed_consistency_residual"]["status"] == "BLOCKED"
    assert "command_torque_contract" in blocked["torque_speed_consistency_residual"]["reason"]


def test_all_three_rw_pipelines_execute_without_claim_inflation(proxy_dataset) -> None:
    expected = {
        "ADCS_RW_FRICTION_INCREASE": "READY_FOR_ASTROGRAPH_REVIEW",
        "ADCS_RW_JAM": "READY_FOR_ASTROGRAPH_REVIEW",
        "ADCS_RW_TORQUE_AUTHORITY_LOSS": "REJECTED_LOCAL_EVIDENCE",
    }
    for fault_id, status in expected.items():
        fault_root, nominal_root = _pair_roots(proxy_dataset, fault_id)
        result = DiagnosticPipelineExecutor().execute(
            fault_root=fault_root, nominal_root=nominal_root, fault_id=fault_id, persist=False
        )
        assert result["execution_status"] == status
        assert result["formal_diagnosis_publishable"] is False
        assert result["formal_model_binding_enabled"] is False
        assert result["local_outputs"]["simulation_evidence_graph"] is not None


def test_external_payloads_cannot_make_sat_sim_publish_a_formal_diagnosis(proxy_dataset) -> None:
    fault_root, nominal_root = _pair_roots(proxy_dataset, "ADCS_RW_JAM")
    result = DiagnosticPipelineExecutor().execute(
        fault_root=fault_root,
        nominal_root=nominal_root,
        persist=False,
        external_stage_outputs={
            "kg_reasoner": {"status": "PASS", "fault": "ADCS_RW_JAM"},
            "fusion": {"status": "PASS", "confidence": 1.0},
            "expert_gate": {"decision": "APPROVE", "reviewer_id": "claimed-expert"},
        },
    )
    assert result["formal_diagnosis_publishable"] is False
    assert result["claim_boundary"]["astrograph_is_authority_for_final_diagnosis_publication"] is True



def test_physics_rejection_cannot_be_cleared_by_later_signature_review(proxy_dataset, monkeypatch) -> None:
    fault_root, nominal_root = _pair_roots(proxy_dataset, "ADCS_RW_JAM")

    def reject_physics(self, *, fault_root, nominal_root):
        return {"decision": "REJECT", "reason": "forced_physics_reject", "pair_id": "forced-pair"}

    monkeypatch.setattr(
        "sat_sim.diagnostic_pipeline_runtime.SimulationValidationAgent.validate_pair",
        reject_physics,
    )
    result = DiagnosticPipelineExecutor().execute(
        fault_root=fault_root,
        nominal_root=nominal_root,
        persist=False,
        external_stage_outputs={
            "kg_reasoner": {"status": "PASS"},
            "fusion": {"status": "PASS"},
            "expert_gate": {"decision": "APPROVE"},
        },
    )
    by_stage = {item["stage_id"]: item for item in result["stage_results"]}
    assert result["execution_status"] == "REJECTED_LOCAL_EVIDENCE"
    assert by_stage["quality_gate"]["status"] == "REJECTED"
    assert by_stage["kg_reasoner"]["status"] == "BLOCKED_UPSTREAM"
    assert by_stage["fusion"]["status"] == "BLOCKED_UPSTREAM"
    assert by_stage["expert_gate"]["status"] == "BLOCKED_UPSTREAM"
    assert result["formal_diagnosis_publishable"] is False

def test_blocked_eps_pipeline_fails_closed(proxy_dataset) -> None:
    fault_root, nominal_root = _pair_roots(proxy_dataset, "ADCS_RW_JAM")
    with pytest.raises(ValueError, match="does not match"):
        DiagnosticPipelineExecutor().execute(
            fault_root=fault_root,
            nominal_root=nominal_root,
            fault_id="EPS_BATTERY_CAPACITY_LOSS",
            persist=False,
        )


def test_pipeline_runtime_api_uses_confined_case_root(proxy_dataset) -> None:
    testclient = pytest.importorskip("fastapi.testclient")
    cases_root = proxy_dataset.campaign.output_root / "cases"
    fault_root, nominal_root = _pair_roots(proxy_dataset, "ADCS_RW_JAM")
    client = testclient.TestClient(create_app(
        runs_root=proxy_dataset.campaign.output_root / "api-runs",
        artifacts_root=proxy_dataset.campaign.output_root / "api-artifacts",
        diagnostic_cases_root=cases_root,
        embedded_worker=False,
        auth_mode="disabled",
    ))
    catalog = client.get("/diagnostics/pipelines")
    assert catalog.status_code == 200
    assert len(catalog.json()["pipelines"]) == 4
    response = client.post(
        "/diagnostics/pipelines/ADCS_RW_JAM/execute",
        json={
            "fault_case_id": fault_root.name,
            "nominal_case_id": nominal_root.name,
            "persist": False,
        },
    )
    assert response.status_code == 200
    assert response.json()["runtime"]["execution_status"] == "READY_FOR_ASTROGRAPH_REVIEW"
    traversal = client.post(
        "/diagnostics/pipelines/ADCS_RW_JAM/execute",
        json={"fault_case_id": "../outside", "nominal_case_id": nominal_root.name},
    )
    assert traversal.status_code == 422
