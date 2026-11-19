from __future__ import annotations

import json
from pathlib import Path
from importlib.metadata import version

import pytest

from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template


WHOLE = "whole_spacecraft.unified_native.v1"
EVENT_START_S = 5.0
EVENT_END_S = 15.0
RUN_DURATION_S = 24.0

ADCS_QOI = [
    "orbit.radius_m",
    "adcs.pointing_error_deg",
    "adcs.rw.speed_rad_s_0",
    "adcs.rw.effective_max_torque_nm_0",
    "adcs.control.applied_torque_nm_0",
    "adcs.sensor.gyro_bias_norm_rad_s",
    "adcs.sensor.gyro_measured_rad_s_x",
    "adcs.sensor.gyro_noise_norm_rad_s",
    "adcs.rw.effective_drag_nms_0",
    "adcs.rw.effective_max_speed_rad_s_0",
]
PROPULSION_QOI = [
    "orbit.radius_m",
    "propulsion.enabled",
    "propulsion.burn_active",
    "propulsion.burn_permitted",
    "propulsion.fuel_mass_kg",
    "propulsion.initial_fuel_mass_kg",
    "propulsion.burn_command_on_time_s",
    "propulsion.nominal_burn_on_time_s",
    "propulsion.total_thrust_n",
    "propulsion.electrical_power_w",
]


def _load_spec(
    run_id: str,
    *,
    domain: str,
    event_kind: str | None = None,
    effect: str | None = None,
    target: str | None = None,
    event_parameters: dict | None = None,
) -> dict:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id=run_id)
    spec["simulation"].update(duration_s=RUN_DURATION_S, step_s=0.2, sample_s=0.2 if domain == "propulsion" else 1.0)
    values = spec["parameters"]["values"]
    values.update(
        initial_pointing_error_deg=8.0,
        initial_soc=0.9,
        rw_initial_speed_rad_s_0=100.0,
    )
    if domain == "propulsion":
        values.update(
            propulsion_enabled=True,
            propulsion_burn_start_s=EVENT_START_S,
            propulsion_burn_on_time_s=4.0,
            propulsion_initial_propellant_kg=1.0,
            propulsion_tank_capacity_kg=2.0,
            propulsion_min_burn_soc=0.05,
            propulsion_thrust_n=5.0,
            propulsion_isp_s=200.0,
        )
        spec["outputs"]["qoi"] = list(PROPULSION_QOI)
    else:
        values["propulsion_enabled"] = False
        spec["outputs"]["qoi"] = list(ADCS_QOI)
    spec["outputs"]["plots"] = []
    spec["outputs"]["telemetry_streams"] = []
    spec["assurance"]["allow_proxy"] = False
    spec["events"] = {"faults": [], "degradations": [], "constraints": []}
    spec["model"]["target"]["mode"] = ("mixed" if event_kind == "constraint" else event_kind) or "nominal"
    if event_kind and effect:
        spec["events"][f"{event_kind}s"] = [{
            "id": f"{effect}_event",
            "effect": effect,
            "target": target or "whole_spacecraft",
            "start_s": 0.0 if effect in {"adcs_rw_jamming", "adcs_reaction_wheel_speed_limit"} else EVENT_START_S,
            "end_s": EVENT_END_S,
            "magnitude": 1.0,
            "parameters": dict(event_parameters or {}),
        }]
    return spec


def _run_case(
    root: Path,
    run_id: str,
    *,
    domain: str,
    event_kind: str | None = None,
    effect: str | None = None,
    target: str | None = None,
    event_parameters: dict | None = None,
):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    assert version("bsk") == "2.11.0"
    spec = _load_spec(
        run_id,
        domain=domain,
        event_kind=event_kind,
        effect=effect,
        target=target,
        event_parameters=event_parameters,
    )
    prepared = prepare_run(spec, output_root=root / "runs", run_id=run_id)
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )
    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    if executed.validation.result.value != "PASS":
        details = executed.validation.model_dump(mode="json")
        print(json.dumps({
            "checks": details.get("checks", []),
            "injection_evidence": details.get("injection_evidence", []),
            "reason_code": details.get("reason_code"),
            "evidence_missing": details.get("evidence_missing", []),
        }, ensure_ascii=False, indent=2, default=str))
        raise AssertionError(f"whole-spacecraft Run Bundle validation did not PASS: {details.get('reason_code')}")
    assert executed.run_record.sealed is True
    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity

    summary = executed.summary
    expected_proxy_count = 5 if domain == "propulsion" else 4
    assert summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert summary["external_or_proxy_module_count"] == expected_proxy_count
    assert summary["runtime_event_count"] == (1 if event_kind else 0)
    if event_kind:
        if event_kind in {"fault", "degradation"}:
            assert summary["fault_environment_episode_count"] == 1
        assert len(executed.validation.injection_evidence) == 1
        evidence = executed.validation.injection_evidence[0]
        assert evidence.delivery_result.value == "PASS"
        assert evidence.effect_result.value == "PASS"
        assert "RUNTIME_EFFECT_VERIFIED" in evidence.reason_codes
    return executed, integrity


def _at(rows, time_s: float) -> dict:
    return min(rows, key=lambda row: abs(float(row["time_s"]) - time_s))


@pytest.fixture(scope="module")
def whole_spacecraft_nominal_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("p9o3-whole-event-bundles")
    adcs, adcs_integrity = _run_case(root, "p9o3_whole_adcs_nominal", domain="adcs")
    propulsion, propulsion_integrity = _run_case(
        root,
        "p9o3_whole_propulsion_nominal",
        domain="propulsion",
    )
    return {
        "root": root,
        "adcs": adcs,
        "adcs_integrity": adcs_integrity,
        "propulsion": propulsion,
        "propulsion_integrity": propulsion_integrity,
    }


@pytest.mark.parametrize(
    ("domain", "event_kind", "effect", "target", "event_parameters"),
    [
        ("adcs", "fault", "adcs_rw_jamming", "whole_spacecraft", {"wheel_index": 0, "brake_torque_nm": 0.2, "lock_tolerance_rad_s": 0.25}),
        ("adcs", "fault", "adcs_rw_motor_failure", "whole_spacecraft", {"wheel_index": 0, "torque_scale": 0.0}),
        ("adcs", "fault", "gyro_bias_step", "whole_spacecraft", {"bias_step_deg_s": [0.5, 0.0, 0.0]}),
        ("adcs", "degradation", "gyro_noise_increase", "whole_spacecraft", {"noise_scale": 100.0}),
        ("adcs", "degradation", "rw_friction_degradation", "whole_spacecraft", {"wheel_index": 0, "drag_nms": 0.012}),
        ("adcs", "constraint", "adcs_reaction_wheel_speed_limit", "whole_spacecraft", {"wheel_index": 0, "max_speed_rad_s": 15.0}),
        ("propulsion", "fault", "propulsion_thruster_ignition_failure", "propulsion", {}),
        ("propulsion", "degradation", "propulsion_burn_impulse_loss", "propulsion", {"remaining_impulse_ratio": 0.25}),
    ],
)
def test_whole_spacecraft_events_have_paired_native_basilisk_runs_and_sealed_bundles(
    whole_spacecraft_nominal_runs,
    domain: str,
    event_kind: str,
    effect: str,
    target: str,
    event_parameters: dict,
) -> None:
    """Verify the eight whole-spacecraft events against same-configuration sealed native baselines."""
    root = whole_spacecraft_nominal_runs["root"]
    run_id = f"p9o3_whole_{effect}"
    injected, injected_integrity = _run_case(
        root,
        run_id,
        domain=domain,
        event_kind=event_kind,
        effect=effect,
        target=target,
        event_parameters=event_parameters,
    )
    nominal = whole_spacecraft_nominal_runs[domain]
    active_window_start = 0.0 if effect in {"adcs_rw_jamming", "adcs_reaction_wheel_speed_limit"} else EVENT_START_S
    active_rows = [
        row for row in injected.trace_rows
        if active_window_start <= float(row["time_s"]) <= EVENT_END_S
    ]
    assert active_rows, injected.trace_rows
    nominal_active = [
        row for row in nominal.trace_rows
        if active_window_start <= float(row["time_s"]) <= EVENT_END_S
    ]
    assert nominal_active, nominal.trace_rows

    if domain == "adcs":
        baseline = _at(nominal.trace_rows, 10.0)
        active = _at(injected.trace_rows, 10.0)
        if effect == "adcs_rw_jamming":
            assert active["adcs.rw.effective_max_torque_nm_0"] == pytest.approx(0.0, abs=1e-9)
            assert baseline["adcs.rw.effective_max_torque_nm_0"] > 0.0
            assert any(row["adcs.control.applied_torque_nm_0"] < 0.0 for row in active_rows)
            restored = _at(injected.trace_rows, 16.0)
            assert restored["adcs.rw.effective_max_torque_nm_0"] > 0.0
            assert restored["label.fault_active"] is False
        elif effect == "adcs_rw_motor_failure":
            assert active["adcs.rw.effective_max_torque_nm_0"] == pytest.approx(0.0, abs=1e-9)
            assert active["adcs.control.applied_torque_nm_0"] == pytest.approx(0.0, abs=1e-9)
            assert baseline["adcs.rw.effective_max_torque_nm_0"] > 0.0
            restored = _at(injected.trace_rows, 16.0)
            assert restored["adcs.rw.effective_max_torque_nm_0"] > 0.0
        elif effect == "gyro_bias_step":
            assert active["adcs.sensor.gyro_bias_norm_rad_s"] == pytest.approx(0.008726646259971648, rel=1e-6)
            assert baseline["adcs.sensor.gyro_bias_norm_rad_s"] == pytest.approx(0.0, abs=1e-12)
            assert active["adcs.sensor.gyro_measured_rad_s_x"] != pytest.approx(baseline["adcs.sensor.gyro_measured_rad_s_x"])
            assert _at(injected.trace_rows, 16.0)["adcs.sensor.gyro_bias_norm_rad_s"] == pytest.approx(0.0, abs=1e-12)
        elif effect == "gyro_noise_increase":
            assert max(row["adcs.sensor.gyro_noise_norm_rad_s"] for row in active_rows) > 1e-5
            assert max(row["adcs.sensor.gyro_noise_norm_rad_s"] for row in nominal_active) == pytest.approx(0.0, abs=1e-12)
            assert _at(injected.trace_rows, 16.0)["adcs.sensor.gyro_noise_norm_rad_s"] == pytest.approx(0.0, abs=1e-12)
        elif effect == "rw_friction_degradation":
            assert active["adcs.rw.effective_drag_nms_0"] == pytest.approx(0.012, rel=1e-8)
            assert baseline["adcs.rw.effective_drag_nms_0"] == pytest.approx(0.0, abs=1e-12)
            assert _at(injected.trace_rows, 16.0)["adcs.rw.effective_drag_nms_0"] == pytest.approx(0.0, abs=1e-12)
        elif effect == "adcs_reaction_wheel_speed_limit":
            assert active["adcs.rw.effective_max_speed_rad_s_0"] == pytest.approx(15.0, abs=1e-9)
            assert baseline["adcs.rw.effective_max_speed_rad_s_0"] == pytest.approx(0.0, abs=1e-12)
            assert any(
                row["adcs.rw.speed_rad_s_0"] > 15.0
                and row["adcs.control.applied_torque_nm_0"] < 0.0
                for row in active_rows
            )
            assert _at(injected.trace_rows, 16.0)["adcs.rw.effective_max_speed_rad_s_0"] == pytest.approx(0.0, abs=1e-12)
    elif effect == "propulsion_thruster_ignition_failure":
        baseline = _at(nominal.trace_rows, 5.4)
        active = _at(injected.trace_rows, 5.4)
        assert active["propulsion.burn_command_on_time_s"] == pytest.approx(0.0, abs=1e-12)
        assert active["propulsion.total_thrust_n"] == pytest.approx(0.0, abs=1e-12)
        assert active["propulsion.burn_active"] == 0
        assert active["propulsion.fuel_mass_kg"] == pytest.approx(active["propulsion.initial_fuel_mass_kg"], abs=1e-9)
        assert baseline["propulsion.burn_command_on_time_s"] == pytest.approx(4.0, abs=1e-12)
        assert baseline["propulsion.total_thrust_n"] > 0.0
        assert nominal.summary["propellant_used_kg"] > 0.0
        assert injected.summary["propellant_used_kg"] == pytest.approx(0.0, abs=1e-12)
    else:
        baseline = _at(nominal.trace_rows, 6.0)
        active = _at(injected.trace_rows, 6.0)
        assert active["propulsion.burn_command_on_time_s"] == pytest.approx(1.0, abs=1e-12)
        assert active["propulsion.burn_command_on_time_s"] < active["propulsion.nominal_burn_on_time_s"]
        assert active["propulsion.total_thrust_n"] > 0.0
        assert active["propulsion.fuel_mass_kg"] < active["propulsion.initial_fuel_mass_kg"]
        assert baseline["propulsion.burn_command_on_time_s"] == pytest.approx(4.0, abs=1e-12)
        assert 0.0 < injected.summary["propellant_used_kg"] < nominal.summary["propellant_used_kg"]
        nominal_orbit_delta = abs(nominal.summary["final_orbit_radius_m"] - nominal.summary["initial_orbit_radius_m"])
        injected_orbit_delta = abs(injected.summary["final_orbit_radius_m"] - injected.summary["initial_orbit_radius_m"])
        assert 0.0 < injected_orbit_delta < nominal_orbit_delta

    report_path = Path("reports/native_whole_spacecraft_events/effect_ab.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "p9-o3.whole-event-ab.v1", "cases": []}
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    direct_fields = (
        ["adcs.rw.effective_max_torque_nm_0", "adcs.control.applied_torque_nm_0",
         "adcs.sensor.gyro_bias_norm_rad_s", "adcs.sensor.gyro_noise_norm_rad_s",
         "adcs.rw.effective_drag_nms_0", "adcs.rw.effective_max_speed_rad_s_0",
         "adcs.rw.speed_rad_s_0"]
        if domain == "adcs"
        else ["propulsion.burn_active", "propulsion.burn_command_on_time_s",
              "propulsion.nominal_burn_on_time_s", "propulsion.total_thrust_n",
              "propulsion.fuel_mass_kg", "propulsion.initial_fuel_mass_kg",
              "propulsion.electrical_power_w", "orbit.radius_m"]
    )
    nominal_row = _at(nominal.trace_rows, 10.0 if domain == "adcs" else 5.4)
    injected_row = _at(injected.trace_rows, 10.0 if domain == "adcs" else 5.4)
    report["cases"].append({
        "domain": domain,
        "event_kind": event_kind,
        "effect_id": effect,
        "nominal_run_id": f"p9o3_whole_{domain}_nominal",
        "injected_run_id": run_id,
        "nominal": {
            "status": nominal.run_record.status.value,
            "sealed": nominal.run_record.sealed,
            "validation": nominal.validation.result.value,
            "summary": nominal.summary,
            "integrity": whole_spacecraft_nominal_runs[f"{domain}_integrity"],
        },
        "injected": {
            "status": injected.run_record.status.value,
            "sealed": injected.run_record.sealed,
            "validation": injected.validation.result.value,
            "summary": injected.summary,
            "integrity": injected_integrity,
            "injection_evidence": [item.model_dump(mode="json") for item in injected.validation.injection_evidence],
        },
        "active_time_s": 10.0 if domain == "adcs" else 5.4,
        "direct_recorder_comparison": {
            field: {"nominal": nominal_row.get(field), "injected": injected_row.get(field)}
            for field in direct_fields
        },
    })
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
