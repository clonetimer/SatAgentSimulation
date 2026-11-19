from __future__ import annotations

from copy import deepcopy

import pytest

from sat_sim.capability_registry import clear_capability_cache, get_adapter_for_capability, get_capability
from sat_sim.effect_evidence import EffectEvidenceClassification, get_effect_evidence_profile
from sat_sim.execution_planner import plan_task_spec
from sat_sim.validation_outcome import ValidationResult, evaluate_validation_outcome

ADCS_ID = "subsystem.adcs_unified_native.v1"
WHOLE_ID = "whole_spacecraft.unified_native.v1"


def _spec(
    capability_id: str,
    *,
    category: str,
    event: dict,
    duration_s: float = 14.0,
    values: dict | None = None,
) -> dict:
    level = "subsystem" if capability_id.startswith("subsystem.") else "whole_spacecraft"
    subsystem = capability_id.split(".")[1] if level == "subsystem" else None
    target = subsystem if level == "subsystem" else "whole_spacecraft"
    outputs = list(
        get_effect_evidence_profile(capability_id, str(event["effect"])).evidence_fields  # type: ignore[union-attr]
    )
    # Templated evidence fields are runtime-only bindings; use wildcard fields in requested QoI.
    outputs = [field.replace("{wheel_index}", "*") for field in outputs if not field.startswith("label.")]
    spec = {
        "schema_version": "1.0.0",
        "task": {"id": f"formal_{event['effect']}", "name": "formal effect evidence"},
        "model": {
            "capability_id": capability_id,
            "target": {"level": level, "name": target, "mode": "mixed" if category == "constraint" else category},
            "config": dict(values or {}),
        },
        "simulation": {
            "level": level,
            "subsystem": subsystem,
            "duration_s": duration_s,
            "step_s": 0.2,
            "sample_s": 1.0,
            "backend": "basilisk",
            "time_base": "simulation_seconds",
            "time_system": "UTC",
        },
        "parameters": {"profile": "demo", "values": dict(values or {}), "overrides": []},
        "events": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {"output_root": "runs/formal_effect", "qoi": outputs, "plots": [], "files": [], "include_summary": True, "include_trace": True, "include_labels": True, "include_manifest": True},
        "assurance": {
            "allow_proxy": False,
            "claim_level": "analysis_only",
            "parameter_profile": "demo",
            "fidelity_level": "declared_by_capability",
            "validation_profile": "default",
        },
    }
    if level == "whole_spacecraft":
        spec["model"]["spacecraft"] = {"mission": {"template": WHOLE_ID}}
    spec["events"][f"{category}s"] = [deepcopy(event)]
    return spec


def _event(effect: str, *, start_s: float = 4.0, end_s: float = 10.0, target: str, **parameters) -> dict:
    return {
        "id": f"event_{effect}",
        "effect": effect,
        "target": target,
        "start_s": start_s,
        "end_s": end_s,
        "parameters": parameters,
    }


def test_unified_effect_contracts_are_declared_and_registered() -> None:
    clear_capability_cache()
    expected = {
        ADCS_ID: {
            "adcs_rw_jamming", "adcs_rw_motor_failure", "adcs_rw_torque_authority_loss",
            "gyro_bias_step", "gyro_noise_increase", "rw_friction_degradation",
            "adcs_reaction_wheel_speed_limit",
        },
        WHOLE_ID: {
            "payload_instrument_off", "comm_data_downlink_link_loss",
            "eps_battery_capacity_loss", "solar_panel_efficiency_loss",
            "thermal_radiator_rejection_loss", "power_safe_mode_threshold",
            "adcs_rw_jamming", "adcs_rw_motor_failure", "gyro_bias_step",
            "gyro_noise_increase", "rw_friction_degradation",
            "adcs_reaction_wheel_speed_limit",
            "propulsion_thruster_ignition_failure", "propulsion_burn_impulse_loss",
        },
        "subsystem.eps.unified_native.v1": {
            "generation_storage_distribution_failure", "energy_margin_degradation",
        },
        "subsystem.comm_data.unified_native.v1": {
            "transmitter_rate_loss", "transmitter_rate_degradation",
        },
    }
    for capability_id, effect_ids in expected.items():
        contract = get_capability(capability_id).operator_contract
        effects = {effect.effect_id: effect for effect in contract.effects}
        for effect_id in effect_ids:
            assert effects[effect_id].verification == "declared"
            profile = get_effect_evidence_profile(capability_id, effect_id)
            assert profile is not None
            assert profile.classification == EffectEvidenceClassification.VERIFIED_NATIVE_EFFECT
            assert profile.assertions
            if capability_id in {
                WHOLE_ID, "subsystem.eps.unified_native.v1", "subsystem.comm_data.unified_native.v1",
            }:
                assert profile.implementation == "basilisk_native"
                declared_fields = contract.observability.effect_evidence[effect_id]
                assert len(declared_fields) == len(set(declared_fields))
                assert set(declared_fields) == set(profile.evidence_fields)
                if capability_id == WHOLE_ID:
                    assert set(effects[effect_id].evidence_fields) == set(profile.evidence_fields)


@pytest.mark.parametrize(
    ("capability_id", "category", "event", "values"),
    [
        (ADCS_ID, "fault", _event("adcs_rw_jamming", target="adcs", wheel_index=0, brake_torque_nm=0.2), {}),
        (ADCS_ID, "fault", _event("adcs_rw_motor_failure", target="adcs", wheel_index=1), {}),
        (ADCS_ID, "degradation", _event("adcs_rw_torque_authority_loss", target="adcs", wheel_index=1, torque_scale=0.25), {}),
        (ADCS_ID, "fault", _event("gyro_bias_step", target="adcs", bias_step_deg_s=[0.0, 0.5, 0.0]), {}),
        (ADCS_ID, "degradation", _event("gyro_noise_increase", target="adcs", noise_scale=100.0), {}),
        (ADCS_ID, "degradation", _event("rw_friction_degradation", target="adcs", wheel_index=2, drag_nms=0.012), {}),
        (ADCS_ID, "constraint", _event("adcs_reaction_wheel_speed_limit", target="adcs", wheel_index=1, max_speed_rad_s=18.0), {}),
        (WHOLE_ID, "fault", _event("payload_instrument_off", target="whole_spacecraft"), {}),
        (WHOLE_ID, "fault", _event("comm_data_downlink_link_loss", target="whole_spacecraft"), {}),
        (WHOLE_ID, "fault", _event("eps_battery_capacity_loss", target="whole_spacecraft", remaining_capacity_ratio=0.55), {}),
        (WHOLE_ID, "degradation", _event("solar_panel_efficiency_loss", target="whole_spacecraft", remaining_efficiency_ratio=0.3), {}),
        (WHOLE_ID, "degradation", _event("thermal_radiator_rejection_loss", target="whole_spacecraft", remaining_rejection_ratio=0.2), {"payload_power_w": 200.0}),
        (WHOLE_ID, "constraint", _event("power_safe_mode_threshold", target="whole_spacecraft", soc_threshold=0.9), {"initial_soc": 0.62}),
        (WHOLE_ID, "fault", _event("adcs_rw_jamming", target="whole_spacecraft", wheel_index=0, brake_torque_nm=0.2), {}),
        (WHOLE_ID, "fault", _event("adcs_rw_motor_failure", target="whole_spacecraft", wheel_index=0), {}),
        (WHOLE_ID, "fault", _event("gyro_bias_step", target="whole_spacecraft", bias_step_deg_s=[0.5, 0.0, 0.0]), {}),
        (WHOLE_ID, "degradation", _event("gyro_noise_increase", target="whole_spacecraft", noise_scale=100.0), {}),
        (WHOLE_ID, "degradation", _event("rw_friction_degradation", target="whole_spacecraft", wheel_index=0, drag_nms=0.012), {}),
        (WHOLE_ID, "constraint", _event("adcs_reaction_wheel_speed_limit", target="whole_spacecraft", wheel_index=0, max_speed_rad_s=5.0), {}),
        (WHOLE_ID, "fault", _event("propulsion_thruster_ignition_failure", target="whole_spacecraft"), {
            "initial_soc": 0.9, "propulsion_enabled": True, "propulsion_burn_start_s": 5.0,
            "propulsion_burn_on_time_s": 8.0, "propulsion_thrust_n": 5.0,
            "propulsion_isp_s": 200.0, "propulsion_initial_propellant_kg": 1.0,
            "propulsion_tank_capacity_kg": 2.0, "propulsion_min_burn_soc": 0.05,
        }),
        (WHOLE_ID, "degradation", _event("propulsion_burn_impulse_loss", target="whole_spacecraft", remaining_impulse_ratio=0.25), {
            "initial_soc": 0.9, "propulsion_enabled": True, "propulsion_burn_start_s": 5.0,
            "propulsion_burn_on_time_s": 8.0, "propulsion_thrust_n": 5.0,
            "propulsion_isp_s": 200.0, "propulsion_initial_propellant_kg": 1.0,
            "propulsion_tank_capacity_kg": 2.0, "propulsion_min_burn_soc": 0.05,
        }),
        ("subsystem.eps.unified_native.v1", "fault", _event("generation_storage_distribution_failure", target="eps"), {}),
        ("subsystem.eps.unified_native.v1", "degradation", _event("energy_margin_degradation", target="eps"), {}),
        ("subsystem.comm_data.unified_native.v1", "fault", _event("transmitter_rate_loss", target="comm_data"), {}),
        ("subsystem.comm_data.unified_native.v1", "degradation", _event("transmitter_rate_degradation", target="comm_data"), {}),
    ],
)
def test_each_unified_effect_has_formal_runtime_pass(capability_id, category, event, values) -> None:
    spec = _spec(capability_id, category=category, event=event, values=values)
    planning = plan_task_spec(spec)
    assert planning.validation.ok, [issue.to_dict() for issue in planning.validation.issues]
    result = get_adapter_for_capability(capability_id).run(spec)
    outcome = evaluate_validation_outcome(
        spec=spec,
        resolved=planning.resolved_spec,
        summary=result.summary,
        trace_rows=result.trace_rows,
    )
    assert len(outcome.injection_evidence) == 1
    evidence = outcome.injection_evidence[0]
    assert evidence.delivery_result == ValidationResult.PASS
    assert evidence.effect_result == ValidationResult.PASS
    assert "RUNTIME_EFFECT_VERIFIED" in evidence.reason_codes
    assert "EFFECT_EVIDENCE_CANDIDATE" not in evidence.reason_codes



def test_whole_spacecraft_task_spec_rejects_runtime_known_but_undeclared_effect() -> None:
    """Advanced TaskSpec admission must not bypass the capability's declared event boundary."""
    spec = _spec(
        WHOLE_ID,
        category="fault",
        event=_event("adcs_rw_jamming", target="whole_spacecraft", wheel_index=0, brake_torque_nm=0.2),
    )
    # The unified runtime knows this IMU effect, but the whole-spacecraft
    # capability has not declared it or registered a whole-graph evidence profile.
    spec["events"]["faults"][0]["effect"] = "adcs_imu_signal_loss"
    planning = plan_task_spec(spec)
    assert not planning.validation.ok
    assert any(issue.severity == "error" for issue in planning.validation.issues)


def test_whole_spacecraft_motor_failure_rejects_partial_torque_parameter() -> None:
    spec = _spec(
        WHOLE_ID,
        category="fault",
        event=_event("adcs_rw_motor_failure", target="whole_spacecraft", wheel_index=0, torque_scale=0.25),
    )
    planning = plan_task_spec(spec)
    assert not planning.validation.ok
    assert any(
        issue.code == "PARAMETER_PATH_UNCONSUMED"
        and "events.faults[0].parameters.torque_scale" in issue.path
        for issue in planning.validation.issues
    )
