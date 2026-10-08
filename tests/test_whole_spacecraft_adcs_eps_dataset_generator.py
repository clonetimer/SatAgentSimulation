from __future__ import annotations

import pytest
import inspect

from scripts.generate_whole_spacecraft_adcs_eps_dataset import (
    CALIBRATION_SOURCES,
    CONDITIONS,
    DECLARED_COMPONENT_FAULTS,
    FAULT_TYPE_TO_CONDITION,
    HR16_MAX_TORQUE_NM,
    PRODUCTION_DURATION_S,
    PRODUCTION_SAMPLE_S,
    PRODUCTION_STEP_S,
    MISSIONS,
    build_plan,
    select_output_row,
    _module_coverage,
    _load_resumable_sample,
    _write_csv,
    _write_json,
    parse_args,
    validate_timing_settings,
)
from sat_sim.bsk_engine.project_native_modules import AdcsSensorFusion
from sat_sim.bsk_engine.unified_native import UnifiedNativeRuntime


def test_plan_is_full_mission_condition_monte_carlo_product() -> None:
    assert len(MISSIONS) == 3
    assert len(CONDITIONS) == 35
    assert CONDITIONS[0].condition_id == "nominal"
    assert sum(item.effect is not None for item in CONDITIONS) == 34
    assert {item.category for item in CONDITIONS} == {"nominal", "fault", "degradation", "constraint"}
    plan = build_plan(
        MISSIONS, CONDITIONS, samples_per_combination=5, base_seed=7,
        duration_s=20.0, step_s=0.2, sample_s=1.0,
    )
    assert len(plan) == 3 * 35 * 5
    assert len({item["metadata"]["sample_id"] for item in plan}) == len(plan)


def test_nominal_and_fault_share_identical_mission_parameters_and_pair_seed() -> None:
    plan = build_plan(
        MISSIONS[:1], CONDITIONS[:2], samples_per_combination=5, base_seed=7,
        duration_s=20.0, step_s=0.2, sample_s=1.0,
    )
    nominal = plan[0]
    fault = plan[5]
    assert nominal["metadata"]["pair_id"] == fault["metadata"]["pair_id"]
    assert nominal["metadata"]["pair_seed"] == fault["metadata"]["pair_seed"]
    assert nominal["metadata"]["spacecraft_parameters"] == fault["metadata"]["spacecraft_parameters"]
    assert all(not values for values in nominal["spec"]["events"].values())
    assert sum(len(values) for values in fault["spec"]["events"].values()) == 1


@pytest.mark.parametrize("count", [4, 11])
def test_sample_count_is_limited_to_requested_range(count: int) -> None:
    with pytest.raises(ValueError, match=r"\[5, 10\]"):
        build_plan(MISSIONS[:1], CONDITIONS[:1], samples_per_combination=count,
                   base_seed=1, duration_s=20.0, step_s=0.2, sample_s=1.0)


def test_output_projection_preserves_full_spacecraft_trace() -> None:
    source = {
        "time_s": 2.0, "orbit.radius_m": 7e6,
        "adcs.pointing_error_deg": 1.2, "eps.battery_soc": 0.6,
        "thermal.payload_temp_k": 300.0, "payload.active": 1,
        "comm.active": 0, "data.storage_bits": 50.0,
        "propulsion.enabled": 1, "event.active_effects": "gyro_bias_step",
    }
    metadata = {
        "sample_id": "sample", "pair_id": "pair", "mission_id": "mission",
        "condition_id": "adcs_gyro_bias_step", "event_category": "fault",
        "effect": "gyro_bias_step",
    }
    projected = select_output_row(source, metadata)
    assert projected["event_active"] == 1
    assert projected["is_nominal"] == 0
    for key in source:
        assert projected[key] == source[key]


def test_selected_architecture_is_four_rw_and_requested_sensor_suite() -> None:
    plan = build_plan(MISSIONS[:1], CONDITIONS[:1], samples_per_combination=5,
                      base_seed=1, duration_s=20.0, step_s=0.2, sample_s=1.0)
    values = plan[0]["metadata"]["spacecraft_parameters"]
    assert values["rw_configuration"] == "pyramid_4"
    assert values["adcs_sensor_suite"] == "star_tracker_imu_plus_backside_sun_earth"


def test_fault_ranges_are_reference_informed_and_traceable() -> None:
    by_id = {item.condition_id: item for item in CONDITIONS}
    assert HR16_MAX_TORQUE_NM == pytest.approx(0.2)
    assert by_id["adcs_rw_jamming"].parameter_ranges["brake_torque_nm"] == pytest.approx((0.18, 0.20))
    assert by_id["adcs_rw_motor_failure"].parameter_ranges["torque_scale"] == (0.0, 0.0)
    assert by_id["adcs_gyro_bias_step"].parameter_ranges["bias_step_deg_s"] == pytest.approx((0.001, 0.010))
    assert by_id["adcs_gyro_noise_increase"].parameter_ranges["noise_scale"] == (8.0, 12.0)
    assert by_id["adcs_rw_bearing_seizure"].parameter_ranges["drag_nms"][1] < 0.001
    for condition in CONDITIONS[1:]:
        assert condition.calibration_source_ids
        assert condition.calibration_confidence in {"high", "medium", "low"}
        assert all(source_id in CALIBRATION_SOURCES for source_id in condition.calibration_source_ids)


def test_sample_metadata_carries_calibration_provenance() -> None:
    plan = build_plan(MISSIONS[:1], CONDITIONS[1:2], samples_per_combination=5,
                      base_seed=1, duration_s=20.0, step_s=0.2, sample_s=1.0)
    calibration = plan[0]["metadata"]["calibration"]
    assert calibration["source_ids"] == ["BASILISK_HR16", "PROJECT_L2_FAULTS"]
    assert calibration["confidence"] == "medium"
    assert calibration["severity_band"] == "severe"


def test_every_selected_component_fault_maps_to_an_executable_condition() -> None:
    condition_ids = {condition.condition_id for condition in CONDITIONS}
    for component, fault_names in DECLARED_COMPONENT_FAULTS.items():
        for fault_name in fault_names:
            key = f"{component}.{fault_name}"
            assert key in FAULT_TYPE_TO_CONDITION
            assert FAULT_TYPE_TO_CONDITION[key] in condition_ids


def test_dataset_attitude_chain_has_no_simplenav_and_fuses_four_sensor_paths() -> None:
    runtime_source = inspect.getsource(UnifiedNativeRuntime.run)
    fusion_source = inspect.getsource(AdcsSensorFusion)
    assert "simpleNav" not in runtime_source and "SimpleNav" not in runtime_source
    for token in ("starInMsg", "imuInMsg", "sunBodyInMsg", "earthBodyInMsg"):
        assert token in fusion_source
    coverage = _module_coverage({"runtime_manifest": {"instantiated_modules": [
        {"tag": tag} for tag in (
            "spacecraft", "eclipse", "reaction_wheels", "imu", "star_tracker",
            "coarse_sun_sensor", "project_sun_direction_sensor", "project_earth_horizon_sensor",
            "project_adcs_sensor_fusion", "solar_panel", "battery", "project_eps_pdu",
            "payload_instrument", "storage", "native_downlink_handling", "thermal_network",
            "propulsion_thrusters", "propulsion_fuel_tank",
        )
    ]}})
    assert coverage["no_truth_navigation_module"] is True


def test_resume_accepts_only_complete_matching_sample(tmp_path) -> None:
    item = build_plan(MISSIONS[:1], CONDITIONS[:1], samples_per_combination=5,
                      base_seed=1, duration_s=20.0, step_s=0.2, sample_s=1.0)[0]
    metadata = item["metadata"]
    sample_dir = tmp_path / metadata["mission_id"] / metadata["condition_id"] / "sample_00"
    telemetry = sample_dir / "telemetry.csv"
    _write_csv(telemetry, [{"time_s": 0.0, "adcs.pointing_error_deg": 1.0}])
    assert _load_resumable_sample(sample_dir, item, tmp_path) is None
    record = {
        **metadata, "row_count": 1, "field_count": 2,
        "telemetry_file": str(telemetry.relative_to(tmp_path)),
        "execution_status": "PASS", "mission_status": "PASS", "overall_status": "PASS",
    }
    _write_json(sample_dir / "sample_metadata.json", {
        **record, "task_spec": item["spec"], "simulation_summary": {}, "module_coverage": {"ok": True},
    })
    resumed = _load_resumable_sample(sample_dir, item, tmp_path)
    assert resumed is not None
    loaded_record, fields, coverage = resumed
    assert loaded_record["sample_id"] == metadata["sample_id"]
    assert fields == {"time_s", "adcs.pointing_error_deg"}
    assert coverage == {"ok": True}
    assert not list(tmp_path.rglob("*.tmp"))


def test_production_timing_defaults_are_aligned_and_observable() -> None:
    args = parse_args([])
    assert (args.duration_s, args.step_s, args.sample_s) == (
        PRODUCTION_DURATION_S, PRODUCTION_STEP_S, PRODUCTION_SAMPLE_S,
    )
    design = validate_timing_settings(args.duration_s, args.step_s, args.sample_s)
    assert design["expected_row_count"] == 601
    assert design["integration_steps_per_sample"] == 5
    assert design["minimum_pre_event_s"] >= 60.0
    assert design["minimum_post_event_s"] >= 180.0


def test_old_short_coarse_time_base_is_rejected_for_production() -> None:
    with pytest.raises(ValueError, match="production timing requirements failed"):
        validate_timing_settings(60.0, 0.2, 1.0)
    smoke = validate_timing_settings(10.0, 0.2, 1.0, allow_short_run=True)
    assert smoke["production_gate_bypassed"] is True
