from __future__ import annotations

from sat_sim.adcs.closed_loop import ADCSClosedLoopConfig, propagate_adcs_closed_loop
from sat_sim.adapters.subsystem_adcs_fidelity import AdcsFidelityAdapter
from sat_sim.form_schema import capability_form_schema, output_label


def _spec(configuration: str) -> dict:
    return {
        "schema_version": "1.0",
        "task_id": f"adcs_{configuration}",
        "task_type": "subsystem",
        "capability_id": "subsystem.adcs_fidelity.v1",
        "target": {"level": "subsystem", "name": "adcs", "mode": "nominal"},
        "simulation": {"duration_s": 4.0, "sample_s": 1.0, "solver": {"step_s": 0.1}},
        "parameters": {"wheel_configuration": configuration, "initial_attitude_error_deg": 5.0},
        "outputs": {"qoi": ["qoi.adcs.final_pointing_error_deg"], "plots": ["adcs.rw.*"]},
    }


def test_three_reaction_wheel_geometries_run_and_have_expected_count() -> None:
    expected = {"orthogonal_3": 3, "four_skew": 4, "pyramid_4": 4}
    for name, count in expected.items():
        config = ADCSClosedLoopConfig.from_task_spec(_spec(name))
        assert config.reaction_wheels.num_wheels == count
        assert len(config.reaction_wheels.axes_body) == count
        samples = propagate_adcs_closed_loop(config)
        assert len(samples[-1].wheel_speed_rad_s) == count
        assert len(samples[-1].wheel_torque_nm) == count


def test_four_wheel_trace_contains_fourth_wheel_outputs() -> None:
    result = AdcsFidelityAdapter().run(_spec("pyramid_4"))
    row = result.trace_rows[-1]
    assert "adcs.rw.speed_rad_s_3" in row
    assert "adcs.rw.command_torque_nm_3" in row
    assert "adcs.rw.momentum_nms_3" in row
    assert row["adcs.control.wheel_configuration"] == "pyramid_4"


def test_quaternion_and_unit_labels_are_semantically_correct() -> None:
    assert "四元数标量" in output_label("adcs.attitude.q_bn_0")
    assert "四元数X分量" in output_label("adcs.attitude.q_bn_1")
    label = output_label("adcs.sensor.gyro_true_rad_s_1")
    assert "弧度" not in label and "时间" not in label


def test_adcs_events_expose_specific_parameters_and_hide_generic_magnitude() -> None:
    schema = capability_form_schema("subsystem.adcs_fidelity.v1")
    faults = {item["effect"]: item for item in schema["event_catalog"]["faults"]}
    degradations = {item["effect"]: item for item in schema["event_catalog"]["degradations"]}
    constraints = {item["effect"]: item for item in schema["event_catalog"]["constraints"]}
    assert faults["adcs_rw_jamming"]["magnitude_applicable"] is False
    assert faults["adcs_rw_motor_failure"]["magnitude_applicable"] is False
    assert [field["name"] for field in faults["adcs_gyro_bias_step"]["parameter_fields"]] == ["bias_deg_s"]
    assert [field["name"] for field in degradations["adcs_gyro_noise_increase"]["parameter_fields"]] == ["noise_multiplier"]
    assert "drag_torque_nm" in [field["name"] for field in degradations["adcs_rw_friction_increase"]["parameter_fields"]]
    assert "max_speed_rad_s" in [field["name"] for field in constraints["adcs_reaction_wheel_speed_limit"]["parameter_fields"]]


def test_binary_jamming_ignores_legacy_magnitude_and_still_acts() -> None:
    spec = _spec("pyramid_4")
    spec["modifiers"] = {
        "faults": [{
            "effect": "adcs_rw_jamming",
            "target": "adcs.reaction_wheel.3",
            "start_s": 1.0,
            "end_s": 3.0,
            "magnitude": 0.0,
            "parameters": {"wheel_index": 3},
        }],
        "degradations": [],
        "constraints": [],
    }
    result = AdcsFidelityAdapter().run(spec)
    active = [row for row in result.trace_rows if row.get("label.fault_active")]
    assert active
    assert all(abs(float(row["adcs.rw.effective_max_torque_nm_3"])) < 1e-12 for row in active)
