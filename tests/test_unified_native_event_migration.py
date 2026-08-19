from __future__ import annotations

from copy import deepcopy

import pytest

from sat_sim.capability_registry import clear_capability_cache, get_adapter_for_capability, get_capability
from sat_sim.bsk_engine.types import BSKEventSpec
from sat_sim.bsk_engine.unified_native import _active_event_ratio
from sat_sim.scenario_templates import instantiate_scenario_template, list_scenario_templates

ADCS_ID = "subsystem.adcs_unified_native.v1"
WHOLE_ID = "whole_spacecraft.unified_native.v1"


def test_active_event_ratio_distinguishes_nominal_and_active_defaults() -> None:
    event = BSKEventSpec(
        event_id="solar_default",
        category="degradation",
        effect="solar_panel_efficiency_loss",
        target="whole_spacecraft",
        start_s=2.0,
        end_s=8.0,
        parameters={},
    )
    assert _active_event_ratio(
        (event,),
        1.0,
        effect=event.effect,
        parameter_names=("remaining_efficiency_ratio",),
        active_default=0.5,
    ) == pytest.approx(1.0)
    assert _active_event_ratio(
        (event,),
        4.0,
        effect=event.effect,
        parameter_names=("remaining_efficiency_ratio",),
        active_default=0.5,
    ) == pytest.approx(0.5)


def _spec(
    capability_id: str,
    *,
    category: str | None = None,
    event: dict | None = None,
    duration_s: float = 16.0,
    values: dict | None = None,
) -> dict:
    spec = {
        "task": {"id": f"test_{capability_id}", "name": "unified native event test"},
        "model": {"capability_id": capability_id},
        "simulation": {
            "level": "subsystem" if capability_id.startswith("subsystem.") else "whole_spacecraft",
            "subsystem": "adcs" if capability_id.startswith("subsystem.") else None,
            "duration_s": duration_s,
            "step_s": 0.2,
            "sample_s": 1.0,
            "backend": "basilisk",
        },
        "assurance": {"allow_proxy": False},
        "parameters": {"values": dict(values or {})},
        "outputs": {"plots": []},
    }
    if category and event:
        spec["events"] = {f"{category}s": [deepcopy(event)]}
    return spec


def _event(effect: str, *, start_s: float = 4.0, end_s: float = 10.0, target: str = "spacecraft", **parameters) -> dict:
    return {
        "id": f"event_{effect}",
        "effect": effect,
        "target": target,
        "start_s": start_s,
        "end_s": end_s,
        "parameters": parameters,
    }


def _during(result, start: float = 4.0, end: float = 10.0):
    return [row for row in result.trace_rows if start <= float(row["time_s"]) <= end]


def test_capability_contracts_and_templates_enable_native_events() -> None:
    clear_capability_cache()
    adcs = get_capability(ADCS_ID).data
    whole = get_capability(WHOLE_ID).data
    assert adcs["modes"]["fault"]["supported"] is True
    assert adcs["modes"]["degradation"]["supported"] is True
    assert whole["modes"]["fault"]["supported"] is True
    assert whole["modes"]["degradation"]["supported"] is True
    for template_id in (
        "adcs_unified_native_event_chain",
        "whole_spacecraft_unified_native_fault_chain",
        "whole_spacecraft_unified_native_degradation_chain",
    ):
        spec = instantiate_scenario_template(template_id)
        assert spec["simulation"]["backend"] == "basilisk"
        assert spec["assurance"]["allow_proxy"] is False
        assert spec.get("events")
    assert list_scenario_templates()["count"] >= 50


@pytest.mark.parametrize(
    ("category", "effect", "parameters", "field", "predicate"),
    [
        ("fault", "adcs_rw_jamming", {"wheel_index": 0, "brake_torque_nm": 0.2}, "adcs.control.applied_torque_nm_0", lambda rows: all(float(r["adcs.control.applied_torque_nm_0"]) <= 0.0 for r in rows[:3])),
        ("fault", "adcs_rw_motor_failure", {"wheel_index": 0, "torque_scale": 0.0}, "adcs.rw.effective_max_torque_nm_0", lambda rows: all(float(r["adcs.rw.effective_max_torque_nm_0"]) == pytest.approx(0.0) for r in rows)),
        ("fault", "gyro_bias_step", {"bias_step_deg_s": [0.5, 0.0, 0.0]}, "adcs.sensor.gyro_bias_rad_s_x", lambda rows: all(abs(float(r["adcs.sensor.gyro_bias_rad_s_x"])) > 1.0e-3 for r in rows)),
        ("degradation", "gyro_noise_increase", {"noise_scale": 100.0}, "adcs.sensor.gyro_noise_rad_s_x", lambda rows: any(abs(float(r["adcs.sensor.gyro_noise_rad_s_x"])) > 1.0e-5 for r in rows)),
        ("degradation", "rw_friction_degradation", {"wheel_index": 0, "drag_nms": 0.01}, "adcs.rw.effective_drag_nms_0", lambda rows: all(float(r["adcs.rw.effective_drag_nms_0"]) == pytest.approx(0.01) for r in rows)),
        ("constraint", "adcs_reaction_wheel_speed_limit", {"wheel_index": 0, "max_speed_rad_s": 15.0}, "adcs.rw.effective_max_speed_rad_s_0", lambda rows: all(float(r["adcs.rw.effective_max_speed_rad_s_0"]) == pytest.approx(15.0) for r in rows)),
    ],
)
def test_adcs_events_modify_pre_dynamics_messages(category, effect, parameters, field, predicate) -> None:
    result = get_adapter_for_capability(ADCS_ID).run(
        _spec(ADCS_ID, category=category, event=_event(effect, target="adcs", **parameters))
    )
    rows = _during(result)
    assert rows and predicate(rows)
    assert all(bool(row[f"label.{category}_active"]) for row in rows)
    assert all(effect in str(row["event.active_effects"]) for row in rows)
    episode = result.metadata["fault_environment"]["episodes"][0]
    assert episode["evidence_status"] == "observed"
    assert episode["evidence_summary"]["physical_effect_verified"] is True
    assert field in {key for row in rows for key in row}
    assert result.summary["external_or_proxy_module_count"] == 0


@pytest.mark.parametrize(
    ("category", "effect", "parameters", "field", "predicate", "values"),
    [
        ("fault", "payload_instrument_off", {}, "payload.active", lambda rows: all(int(r["payload.active"]) == 0 and float(r["payload.generated_bps"]) == 0.0 for r in rows), {}),
        ("fault", "comm_data_downlink_link_loss", {}, "comm.active", lambda rows: all(int(r["comm.active"]) == 0 and float(r["comm.downlink_bps"]) == 0.0 for r in rows), {}),
        ("fault", "eps_battery_capacity_loss", {"remaining_capacity_ratio": 0.5}, "eps.battery_capacity_j", lambda rows: all(float(r["eps.battery_capacity_j"]) == pytest.approx(288000.0) for r in rows), {}),
        ("degradation", "solar_panel_efficiency_loss", {"remaining_efficiency_ratio": 0.2}, "eps.solar_array_power_w", lambda rows: max(float(r["eps.solar_array_power_w"]) for r in rows) < 300.0, {}),
        ("degradation", "thermal_radiator_rejection_loss", {"remaining_rejection_ratio": 0.1}, "thermal.payload_temp_k", lambda rows: all(float(r["thermal.payload_temp_k"]) > 0.0 for r in rows), {"payload_power_w": 200.0}),
        ("constraint", "power_safe_mode_threshold", {"soc_threshold": 0.9}, "label.power_safe_mode_engaged", lambda rows: all(bool(r["label.power_safe_mode_engaged"]) and int(r["payload.active"]) == 0 and int(r["comm.active"]) == 0 for r in rows), {"initial_soc": 0.62}),
    ],
)
def test_whole_spacecraft_events_propagate_before_state_integration(category, effect, parameters, field, predicate, values) -> None:
    result = get_adapter_for_capability(WHOLE_ID).run(
        _spec(WHOLE_ID, category=category, event=_event(effect, target="whole_spacecraft", **parameters), values=values)
    )
    rows = _during(result)
    assert rows and predicate(rows)
    assert all(effect in str(row["event.active_effects"]) for row in rows)
    episode = result.metadata["fault_environment"]["episodes"][0]
    assert episode["evidence_status"] == "observed"
    assert episode["evidence_summary"]["physical_effect_verified"] is True
    assert field in rows[0]
    assert result.summary["energy_bounds_pass"] is True
    assert result.summary["data_bounds_pass"] is True
    assert result.summary["external_or_proxy_module_count"] == 4


def test_thermal_degradation_changes_recorder_temperature_against_nominal() -> None:
    adapter = get_adapter_for_capability(WHOLE_ID)
    nominal = adapter.run(
        _spec(
            WHOLE_ID,
            duration_s=26.0,
            values={"payload_power_w": 200.0, "initial_payload_temp_k": 339.0},
        )
    )
    degraded = adapter.run(
        _spec(
            WHOLE_ID,
            category="degradation",
            event=_event("thermal_radiator_rejection_loss", end_s=22.0, remaining_rejection_ratio=0.1),
            duration_s=26.0,
            values={"payload_power_w": 200.0, "initial_payload_temp_k": 339.0},
        )
    )
    nominal_20 = next(row for row in nominal.trace_rows if float(row["time_s"]) == 20.0)
    degraded_20 = next(row for row in degraded.trace_rows if float(row["time_s"]) == 20.0)
    assert float(degraded_20["thermal.radiator_rejection_ratio"]) == pytest.approx(0.1)
    assert float(degraded_20["thermal.payload_radiator_rejected_w"]) < float(
        nominal_20["thermal.payload_radiator_rejected_w"]
    )
    # The 10-second active interval is intentionally short relative to the
    # payload thermal time constant.  Require the physically correct direction,
    # while the direct radiator-power evidence above proves the commanded loss.
    assert float(degraded_20["thermal.payload_temp_k"]) > float(nominal_20["thermal.payload_temp_k"])


def test_unmigrated_effect_and_invalid_wheel_are_rejected() -> None:
    adapter = get_adapter_for_capability(ADCS_ID)
    unsupported = _spec(ADCS_ID, category="fault", event=_event("payload_instrument_off"))
    issues = adapter.validate(unsupported)
    assert any(issue.code == "event_effect" and issue.severity == "error" for issue in issues)
    invalid = _spec(
        ADCS_ID,
        category="fault",
        event=_event("adcs_rw_jamming", wheel_index=7),
    )
    issues = adapter.validate(invalid)
    assert any(issue.code == "range" and "wheel_index" in issue.path for issue in issues)
