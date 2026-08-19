from __future__ import annotations

from sat_sim.adapters.subsystem_adcs_bsksim import AdcsBSKSimAdapter
from sat_sim.adapters.whole_spacecraft_bsksim_foundation import WholeSpacecraftBSKSimFoundationAdapter
from sat_sim.bsk_engine.event_manager import parse_bsk_events
from sat_sim.fault_environment import BSKRLStyleFaultAdapter, get_fault_contract


def _adcs_spec():
    return {
        "task_id": "test_adcs_fault_episode",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_bsksim.v1",
        "model": {"capability_id": "subsystem.adcs_bsksim.v1", "target": {"level": "subsystem", "name": "adcs", "mode": "nadir"}},
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {"level": "subsystem", "subsystem": "adcs", "duration_s": 12.0, "step_s": 0.5, "sample_s": 1.0},
        "parameters": {"values": {"initial_attitude_error_deg": 6.0, "target_mode": "nadir", "wheel_configuration": "pyramid_4"}},
        "outputs": {"plots": ["adcs.pointing.error_deg", "adcs.rw.speed_rad_s_*"]},
        "events": {
            "faults": [{"id": "rw0_jam", "effect": "adcs_rw_jamming", "target": "rw_0", "start_s": 3.0, "duration_s": 3.0, "parameters": {"wheel_index": 0}}],
            "degradations": [{"id": "gyro_noise", "effect": "gyro_noise_increase", "target": "gyro", "start_s": 5.0, "duration_s": 4.0, "parameters": {"noise_scale": 3.0}}],
            "constraints": [{"id": "rw1_limit", "effect": "adcs_reaction_wheel_speed_limit", "target": "rw_1", "start_s": 6.0, "duration_s": 2.0, "parameters": {"wheel_index": 1, "max_speed_rad_s": 40.0}}],
        },
    }


def _foundation_spec():
    return {
        "task_id": "foundation_fault_episode",
        "capability_id": "whole_spacecraft.bsksim_foundation.v1",
        "model": {"capability_id": "whole_spacecraft.bsksim_foundation.v1", "target": {"level": "whole_spacecraft", "name": "whole_spacecraft", "mode": "inertialPoint"}},
        "simulation": {"level": "whole_spacecraft", "duration_s": 10.0, "step_s": 1.0, "sample_s": 2.0, "backend": "bsksim_style"},
        "parameters": {"values": {"initial_pointing_error_deg": 5.0, "orbit_rate_rad_s": 0.0011}},
        "events": {"faults": [{"id": "marker", "effect": "bsksim_fault_marker", "target": "spacecraft", "start_s": 4.0, "duration_s": 3.0}], "degradations": [], "constraints": []},
        "outputs": {"plots": ["attitude.pointing_error_deg", "orbit.theta_rad"]},
    }


def test_v0542_catalog_keeps_fault_degradation_constraint_separate():
    assert get_fault_contract("adcs_rw_jamming", "fault").category == "fault"
    assert get_fault_contract("gyro_noise_increase", "degradation").category == "degradation"
    constraint = get_fault_contract("adcs_reaction_wheel_speed_limit", "constraint")
    assert constraint.category == "constraint"
    assert "不作为故障" in constraint.trigger_semantics


def test_v0542_episode_adapter_builds_category_counts():
    events = parse_bsk_events(_adcs_spec())
    trace_rows = [
        {"time_s": 2.0, "label.fault_active": False, "label.degradation_active": False, "label.constraint_active": False, "adcs.event.active_effects": ""},
        {"time_s": 4.0, "label.fault_active": True, "label.degradation_active": False, "label.constraint_active": False, "adcs.event.active_effects": "adcs_rw_jamming", "adcs.rw.speed_rad_s_0": 10.0},
        {"time_s": 6.0, "label.fault_active": True, "label.degradation_active": True, "label.constraint_active": True, "adcs.event.active_effects": "adcs_rw_jamming,gyro_noise_increase,adcs_reaction_wheel_speed_limit", "adcs.rw.effective_max_speed_rad_s_1": 40.0, "adcs.sensor.effective_gyro_noise_scale": 3.0, "adcs.rw.speed_rad_s_0": 0.0},
        {"time_s": 10.0, "label.fault_active": False, "label.degradation_active": False, "label.constraint_active": False, "adcs.event.active_effects": ""},
    ]
    snapshot = BSKRLStyleFaultAdapter().summarize(events, trace_rows).to_dict()
    assert snapshot["style"] == "bsk_rl_style_no_gym_dependency"
    assert snapshot["categories"] == {"fault": 1, "degradation": 1, "constraint": 1}
    assert all(ep["evidence_status"] == "observed" for ep in snapshot["episodes"])


def test_v0542_adcs_bsksim_run_contains_episode_evidence():
    result = AdcsBSKSimAdapter().run(_adcs_spec())
    fault_env = result.metadata["fault_environment"]
    assert fault_env["episode_count"] == 3
    assert fault_env["categories"] == {"fault": 1, "degradation": 1, "constraint": 1}
    assert result.summary["fault_environment_style"] == "bsk_rl_style_no_gym_dependency"
    assert result.summary["fault_environment_episode_count"] == 3
    assert any(ep["effect"] == "adcs_rw_jamming" for ep in fault_env["episodes"])
    assert any(row.get("label.fault_active") for row in result.trace_rows)


def test_v0542_foundation_run_preserves_fault_environment_metadata():
    result = WholeSpacecraftBSKSimFoundationAdapter().run(_foundation_spec())
    assert result.summary["fault_environment_style"] == "bsk_rl_style_no_gym_dependency"
    assert result.labels["fault_environment"]["episode_count"] == 1
    assert result.metadata["fault_environment"]["categories"]["fault"] == 1
