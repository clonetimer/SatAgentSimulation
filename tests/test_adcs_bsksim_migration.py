from __future__ import annotations

from sat_sim.adapters.subsystem_adcs_bsksim import AdcsBSKSimAdapter
from sat_sim.bsk_engine.adcs_scenario import ADCSBSKSimScenario, adcs_config_from_task_spec


def _spec():
    return {
        "task_id": "test_adcs_bsksim",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_bsksim.v1",
        "model": {"capability_id": "subsystem.adcs_bsksim.v1", "target": {"level": "subsystem", "name": "adcs", "mode": "nadir"}},
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {"level": "subsystem", "subsystem": "adcs", "duration_s": 10.0, "step_s": 0.5, "sample_s": 1.0},
        "parameters": {"values": {"initial_attitude_error_deg": 6.0, "target_mode": "nadir", "wheel_configuration": "pyramid_4"}},
        "outputs": {"plots": ["adcs.attitude.pointing_error_deg", "adcs.rw.speed_rad_s_*"]},
        "modifiers": {"constraints": [{"effect": "adcs_reaction_wheel_speed_limit", "target": "rw_0", "start_s": 4.0, "duration_s": 3.0, "parameters": {"wheel_index": 0, "max_speed_rad_s": 50.0}}]},
    }


def test_adcs_bsksim_execution_plan_contains_adcs_modules():
    scenario = ADCSBSKSimScenario(adcs_config_from_task_spec(_spec()))
    plan = scenario.build_execution_plan()
    tags = {m.tag for m in plan.modules}
    assert {"spacecraft", "reaction_wheels", "mode_request", "mrp_feedback", "rw_torque_mapper"}.issubset(tags)
    assert len(plan.processes) == 2
    assert len(plan.tasks) == 2
    assert plan.mode_request == "nadir"


def test_adcs_bsksim_run_uses_event_manager_and_bridge():
    result = AdcsBSKSimAdapter().run(_spec())
    assert result.summary["capability_id"] == "subsystem.adcs_bsksim.v1"
    assert result.summary["engine"] == "project_bsksim_style_adcs"
    assert result.summary["bsk_module_count"] >= 10
    assert result.metadata["model_source_boundary"]["physics_bridge"].endswith("AdcsFidelityAdapter")
    assert any(row.get("label.constraint_active") for row in result.trace_rows)
    assert any("adcs_reaction_wheel_speed_limit" in row.get("adcs.event.active_effects", "") for row in result.trace_rows)


def test_adcs_bsksim_validation_accepts_projection():
    issues = AdcsBSKSimAdapter().validate(_spec())
    assert not issues
