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
    target = "adcs" if level == "subsystem" else "whole_spacecraft"
    outputs = list(
        get_effect_evidence_profile(capability_id, str(event["effect"])).evidence_fields  # type: ignore[union-attr]
    )
    # Templated evidence fields are runtime-only bindings; use wildcard fields in requested QoI.
    outputs = [field.replace("{wheel_index}", "*") for field in outputs]
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
            "subsystem": "adcs" if level == "subsystem" else None,
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
            "adcs_rw_jamming", "adcs_rw_motor_failure", "gyro_bias_step",
            "gyro_noise_increase", "rw_friction_degradation",
            "adcs_reaction_wheel_speed_limit",
        },
        WHOLE_ID: {
            "payload_instrument_off", "comm_data_downlink_link_loss",
            "eps_battery_capacity_loss", "solar_panel_efficiency_loss",
            "thermal_radiator_rejection_loss", "power_safe_mode_threshold",
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


@pytest.mark.parametrize(
    ("capability_id", "category", "event", "values"),
    [
        (ADCS_ID, "fault", _event("adcs_rw_jamming", target="adcs", wheel_index=0, brake_torque_nm=0.2), {}),
        (ADCS_ID, "fault", _event("adcs_rw_motor_failure", target="adcs", wheel_index=1, torque_scale=0.25), {}),
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
