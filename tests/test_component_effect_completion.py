from __future__ import annotations

import math

from sat_sim.capability_registry import get_adapter_for_capability, get_capability


CASES = (
    ("component.ground_station.v1", "ground_station", "tracking_loss", "gover_tdrift",
     "ground_station.has_access", "lower", "ground_station.max_range_m", "lower"),
    ("component.antenna.v1", "antenna", "gain_drop", "surface_contamination",
     "comm.antenna.gain_dbi", "lower", "comm.antenna.gain_dbi", "lower"),
    ("component.transmitter.v1", "transmitter", "power_amplifier_fault", "output_power_decay",
     "comm.transmitter.tx_power_w", "lower", "comm.transmitter.tx_power_w", "lower"),
    ("component.cmg.v1", "cmg", "gimbal_stuck", "gimbal_friction_growth",
     "adcs.cmg.commanded_gimbal_rate_rad_s", "lower", "adcs.cmg.commanded_gimbal_rate_rad_s", "lower"),
    ("component.fuel_tank.v1", "fuel_tank", "pressure_sensor_fault", "pressure_decay",
     "propulsion.fuel_tank.pressure_pa", "lower", "propulsion.fuel_tank.pressure_pa", "lower"),
    ("component.imu.v1", "imu", "bias_step", "bias_drift",
     "adcs.imu.gyro_x_rad_s", "higher", "adcs.imu.gyro_x_rad_s", "higher"),
    ("component.magnetometer.v1", "magnetometer", "axis_bias", "bias_drift",
     "adcs.magnetometer.field_x_t", "higher", "adcs.magnetometer.field_x_t", "higher"),
    ("component.pdu.v1", "pdu", "over_current", "contact_resistance_growth",
     "eps.pdu.requested_power_w", "higher", "eps.pdu.delivered_power_w", "lower"),
    ("component.star_tracker.v1", "star_tracker", "misalignment", "centroid_noise_growth",
     "adcs.star_tracker.sigma_x", "higher", "adcs.star_tracker.sigma_x", "higher"),
    ("component.sun_sensor.v1", "sun_sensor", "contamination", "sensitivity_decay",
     "adcs.sun_sensor.intensity", "lower", "adcs.sun_sensor.intensity", "lower"),
    ("component.thruster.v1", "thruster", "nozzle_blockage", "thrust_coefficient_drift",
     "propulsion.thruster.effective_thrust_n", "lower", "propulsion.thruster.effective_thrust_n", "lower"),
)


def _spec(capability_id: str, target: str, mode: str, kind: str | None = None, effect: str | None = None) -> dict:
    modifiers: dict[str, list[dict]] = {}
    if kind and effect:
        modifiers[kind] = [{
            "modifier_type": effect,
            "start_s": 2.0,
            "duration_s": 6.0,
            "severity": 0.8,
            "parameters": {"rate_per_s": 0.2, "max_fraction": 0.8},
        }]
    return {
        "task_id": f"{target}_{mode}",
        "task_type": "component",
        "capability_id": capability_id,
        "target": {"level": "component", "name": target, "mode": mode},
        "simulation": {"duration_s": 10.0, "sample_s": 1.0},
        "parameters": {},
        "modifiers": modifiers,
    }


def _assert_direction(actual: object, baseline: object, direction: str, case: str) -> None:
    if isinstance(actual, bool) and isinstance(baseline, bool):
        assert int(actual) < int(baseline) if direction == "lower" else int(actual) > int(baseline), case
        return
    actual_value = float(actual)
    baseline_value = float(baseline)
    assert math.isfinite(actual_value)
    if direction == "lower":
        assert actual_value < baseline_value, case
    else:
        assert actual_value > baseline_value, case


def test_remaining_components_have_windowed_fault_and_degradation_physics() -> None:
    for capability_id, target, fault, degradation, fault_field, fault_direction, degradation_field, degradation_direction in CASES:
        adapter = get_adapter_for_capability(capability_id)
        contract = get_capability(capability_id)
        nominal = adapter.run(_spec(capability_id, target, "nominal"), contract.data)
        fault_spec = _spec(capability_id, target, "fault", "faults", fault)
        degradation_spec = _spec(capability_id, target, "degradation", "degradations", degradation)
        assert not [issue for issue in adapter.validate(fault_spec, contract.data) if issue.severity == "error"]
        assert not [issue for issue in adapter.validate(degradation_spec, contract.data) if issue.severity == "error"]
        fault_result = adapter.run(fault_spec, contract.data)
        degradation_result = adapter.run(degradation_spec, contract.data)

        nominal_active = next(row for row in nominal.trace_rows if row["time_s"] == 6.0)
        fault_active = next(row for row in fault_result.trace_rows if row["time_s"] == 6.0)
        degradation_active = next(row for row in degradation_result.trace_rows if row["time_s"] == 6.0)
        assert fault_active["label.fault_active"] is True
        assert degradation_active["label.degradation_active"] is True
        assert fault_result.trace_rows[0]["label.fault_active"] is False
        assert fault_result.trace_rows[-1]["label.fault_active"] is False
        assert degradation_result.trace_rows[-1]["label.degradation_active"] is False
        _assert_direction(fault_active[fault_field], nominal_active[fault_field], fault_direction, capability_id)
        _assert_direction(
            degradation_active[degradation_field],
            nominal_active[degradation_field],
            degradation_direction,
            capability_id,
        )


def test_remaining_components_reject_unknown_effects() -> None:
    for capability_id, target, *_ in CASES:
        adapter = get_adapter_for_capability(capability_id)
        spec = _spec(capability_id, target, "fault", "faults", "invented_effect")
        issues = adapter.validate(spec, get_capability(capability_id).data)
        assert any(issue.severity == "error" and "unsupported" in issue.message for issue in issues)
