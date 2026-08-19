from __future__ import annotations

from sat_sim.adapters.whole_spacecraft_bsksim_foundation import WholeSpacecraftBSKSimFoundationAdapter
from sat_sim.bsk_engine import create_scenario
from sat_sim.capability_registry import get_adapter_for_capability, get_capability
from sat_sim.scenario_templates import list_scenario_templates


def _foundation_spec() -> dict:
    return {
        "schema_version": "1.0.0",
        "task": {"id": "bsksim_foundation_demo", "name": "BSKSim基础场景"},
        "simulation": {
            "level": "whole_spacecraft",
            "duration_s": 10.0,
            "step_s": 1.0,
            "sample_s": 5.0,
            "backend": "basilisk",
        },
        "parameters": {"profile": "demo", "values": {"initial_pointing_error_deg": 5.0, "orbit_rate_rad_s": 0.0011}, "overrides": []},
        "events": {
            "faults": [{"id": "marker", "effect": "bsksim_fault_marker", "target": "spacecraft", "start_s": 5.0}],
            "degradations": [],
            "constraints": [],
        },
        "outputs": {
            "output_root": "runs",
            "trace_format": "csv",
            "qoi": [],
            "plots": ["attitude.pointing_error_deg", "orbit.theta_rad"],
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
        },
        "assurance": {
            "fidelity_level": "engineering_preview",
            "claim_level": "analysis_only",
            "validation_profile": "default",
            "parameter_profile": "demo",
            "allow_proxy": False,
        },
        "model": {
            "capability_id": "whole_spacecraft.bsksim_foundation.v1",
            "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "inertialPoint"},
            "config": {},
        },
        "provenance": {"fields": {}, "assumptions": [], "pending_confirmations": []},
        "metadata": {},
    }


def test_v0540_capability_registry_exposes_bsksim_foundation() -> None:
    contract = get_capability("whole_spacecraft.bsksim_foundation.v1")
    assert contract.target_level == "whole_spacecraft"
    assert contract.trust_level == "engineering_preview"
    assert contract.adapter_class_path.endswith("WholeSpacecraftBSKSimFoundationAdapter")
    assert isinstance(get_adapter_for_capability(contract.capability_id), WholeSpacecraftBSKSimFoundationAdapter)


def test_v0540_scenario_plan_has_bsk_style_process_task_split() -> None:
    scenario = create_scenario(_foundation_spec())
    plan = scenario.build_execution_plan().to_dict()
    assert plan["engine"] == "basilisk_native_foundation"
    assert {p["name"] for p in plan["processes"]} == {"DynamicsProcess", "FswProcess"}
    assert {t["name"] for t in plan["tasks"]} == {"DynamicsTask", "FswTask"}
    assert any(m["tag"] == "mode_request" for m in plan["modules"])
    assert any(c["target"] == "mrp_feedback.guidInMsg" for c in plan["connections"])


def test_v0540_adapter_runs_basilisk_timeline_and_preserves_event_labels() -> None:
    result = WholeSpacecraftBSKSimFoundationAdapter().run(_foundation_spec())
    assert result.summary["status"] == "complete"
    assert result.summary["process_count"] == 2
    assert result.summary["task_count"] == 2
    assert result.summary["execution_plan_available"] is True
    assert result.trace_rows[0]["label.fault_active"] is False
    assert any(row["label.fault_active"] is True for row in result.trace_rows)
    assert "execution_plan" in result.metadata
    assert result.metadata["model_source_boundary"]["native_module_migration"] == "foundation_orbit_attitude_native_modules"
    assert result.summary["instantiated_module_count"] == 7
    assert result.summary["runtime_truth_status"] == "instantiated_connected_recorded"


def test_v0540_template_catalog_includes_foundation_scenario() -> None:
    payload = list_scenario_templates()
    templates = payload["templates"]
    matches = [t for t in templates if t["template_id"] == "bsksim_foundation_orbit_attitude"]
    assert matches
    assert matches[0]["capability_id"] == "whole_spacecraft.bsksim_foundation.v1"
    assert matches[0]["name"] == "BSKSim基础轨道姿态场景"
