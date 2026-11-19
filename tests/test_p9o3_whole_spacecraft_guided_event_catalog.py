from __future__ import annotations

from sat_sim.capability_registry import get_capability
from sat_sim.effect_evidence import EffectEvidenceClassification, get_effect_evidence_profile
from sat_sim.form_schema import capability_form_schema


WHOLE = "whole_spacecraft.unified_native.v1"

VERIFIED_GUIDED_EFFECTS = {
    "fault": {
        "payload_instrument_off",
        "comm_data_downlink_link_loss",
        "eps_battery_capacity_loss",
        "adcs_rw_jamming",
        "adcs_rw_motor_failure",
        "gyro_bias_step",
        "propulsion_thruster_ignition_failure",
    },
    "degradation": {
        "solar_panel_efficiency_loss",
        "thermal_radiator_rejection_loss",
        "gyro_noise_increase",
        "rw_friction_degradation",
        "propulsion_burn_impulse_loss",
    },
    "constraint": {
        "power_safe_mode_threshold",
        "adcs_reaction_wheel_speed_limit",
    },
}

UNDECLARED_RUNTIME_EFFECTS = {"adcs_imu_signal_loss"}


def test_whole_spacecraft_guided_event_catalog_is_limited_to_verified_native_profiles() -> None:
    schema = capability_form_schema(WHOLE)
    catalog = schema["event_catalog"]
    exposed = {
        kind: {item["effect"] for item in catalog[kind + "s"]}
        for kind in ("fault", "degradation", "constraint")
    }

    assert exposed == VERIFIED_GUIDED_EFFECTS
    declared_modes = get_capability(WHOLE).data["modes"]
    declared = {
        effect
        for kind in ("fault", "degradation", "constraint")
        for effect in declared_modes[kind].get("effects", [])
    }
    assert declared == set().union(*VERIFIED_GUIDED_EFFECTS.values())
    assert UNDECLARED_RUNTIME_EFFECTS.isdisjoint(declared)
    assert set().union(*exposed.values()) == set().union(*VERIFIED_GUIDED_EFFECTS.values())
    assert UNDECLARED_RUNTIME_EFFECTS.isdisjoint(set().union(*exposed.values()))
    trace_fields = set(schema["outputs"]["trace_fields"])
    assert {
        "adcs.rw.effective_torque_ratio_0",
        "adcs.sensor.gyro_bias_norm_rad_s",
        "adcs.sensor.gyro_noise_norm_rad_s",
        "propulsion.initial_propellant_kg",
        "propulsion.propellant_used_kg",
    } <= trace_fields

    for effects in exposed.values():
        for effect_id in effects:
            profile = get_effect_evidence_profile(WHOLE, effect_id)
            assert profile is not None, effect_id
            assert profile.classification == EffectEvidenceClassification.VERIFIED_NATIVE_EFFECT, effect_id
            assert profile.implementation == "basilisk_native", effect_id
            assert profile.assertions, effect_id


def test_whole_spacecraft_guided_event_catalog_hides_runtime_effects_without_whole_spacecraft_profiles() -> None:
    schema = capability_form_schema(WHOLE)
    exposed = {
        item["effect"]
        for kind in ("faults", "degradations", "constraints")
        for item in schema["event_catalog"][kind]
    }

    assert UNDECLARED_RUNTIME_EFFECTS.isdisjoint(exposed)
    assert schema["event_catalog"]["timed_degradation_supported"] is True


def test_whole_spacecraft_event_templates_expose_verified_parameters_with_physical_defaults() -> None:
    schema = capability_form_schema(WHOLE)
    templates = {
        item["effect"]: item
        for kind in ("faults", "degradations", "constraints")
        for item in schema["event_catalog"][kind]
    }

    assert templates["adcs_rw_jamming"]["default_event"]["parameters"] == {
        "wheel_index": 0,
        "brake_torque_nm": 0.2,
    }
    motor_failure = templates["adcs_rw_motor_failure"]
    assert motor_failure["default_event"]["parameters"] == {"wheel_index": 0}
    assert {field["name"] for field in motor_failure["parameter_fields"]} == {"wheel_index"}
    assert templates["gyro_bias_step"]["default_event"]["parameters"]["bias_step_deg_s"] == [0.2, 0.0, 0.0]
    assert templates["gyro_noise_increase"]["default_event"]["parameters"]["noise_scale"] == 10.0
    assert templates["rw_friction_degradation"]["default_event"]["parameters"] == {
        "wheel_index": 0,
        "drag_nms": 0.012,
    }
    assert templates["adcs_reaction_wheel_speed_limit"]["default_event"]["parameters"]["max_speed_rad_s"] == 5.0
    assert templates["propulsion_thruster_ignition_failure"]["default_event"]["parameters"] == {}
    assert templates["propulsion_burn_impulse_loss"]["default_event"]["parameters"]["remaining_impulse_ratio"] == 0.25
