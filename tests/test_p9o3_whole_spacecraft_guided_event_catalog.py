from __future__ import annotations

from sat_sim.effect_evidence import EffectEvidenceClassification, get_effect_evidence_profile
from sat_sim.form_schema import capability_form_schema


WHOLE = "whole_spacecraft.unified_native.v1"

VERIFIED_GUIDED_EFFECTS = {
    "fault": {
        "payload_instrument_off",
        "comm_data_downlink_link_loss",
        "eps_battery_capacity_loss",
    },
    "degradation": {
        "solar_panel_efficiency_loss",
        "thermal_radiator_rejection_loss",
    },
    "constraint": {
        "power_safe_mode_threshold",
    },
}

UNVERIFIED_WHOLE_EFFECTS = {
    "adcs_rw_jamming",
    "adcs_rw_motor_failure",
    "gyro_bias_step",
    "gyro_noise_increase",
    "rw_friction_degradation",
    "adcs_reaction_wheel_speed_limit",
    "propulsion_thruster_ignition_failure",
    "propulsion_burn_impulse_loss",
}


def test_whole_spacecraft_guided_event_catalog_is_limited_to_verified_native_profiles() -> None:
    schema = capability_form_schema(WHOLE)
    catalog = schema["event_catalog"]
    exposed = {
        kind: {item["effect"] for item in catalog[kind + "s"]}
        for kind in ("fault", "degradation", "constraint")
    }

    assert exposed == VERIFIED_GUIDED_EFFECTS
    assert set().union(*exposed.values()) == {
        "payload_instrument_off",
        "comm_data_downlink_link_loss",
        "eps_battery_capacity_loss",
        "solar_panel_efficiency_loss",
        "thermal_radiator_rejection_loss",
        "power_safe_mode_threshold",
    }
    assert not (UNVERIFIED_WHOLE_EFFECTS & set().union(*exposed.values()))

    for effects in exposed.values():
        for effect_id in effects:
            profile = get_effect_evidence_profile(WHOLE, effect_id)
            assert profile is not None, effect_id
            assert profile.classification == EffectEvidenceClassification.VERIFIED_NATIVE_EFFECT, effect_id
            assert profile.implementation == "basilisk_native", effect_id
            assert profile.assertions, effect_id


def test_whole_spacecraft_guided_event_catalog_hides_unverified_adcs_and_propulsion_events() -> None:
    schema = capability_form_schema(WHOLE)
    exposed = {
        item["effect"]
        for kind in ("faults", "degradations", "constraints")
        for item in schema["event_catalog"][kind]
    }

    assert UNVERIFIED_WHOLE_EFFECTS.isdisjoint(exposed)
    assert schema["event_catalog"]["timed_degradation_supported"] is True
