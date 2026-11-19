from __future__ import annotations

import json
from importlib.metadata import version
from pathlib import Path
from typing import Any

import pytest

from sat_sim.effect_evidence import get_effect_evidence_profile
from sat_sim.run_bundle import execute_prepared_run, prepare_run, verify_run_bundle


WHOLE = "whole_spacecraft.unified_native.v1"
EVENT_START_S = 4.0
EVENT_END_S = 10.0
RUN_DURATION_S = 14.0

ADCS_EFFECTS = (
    "adcs_rw_jamming",
    "adcs_rw_motor_failure",
    "gyro_bias_step",
    "gyro_noise_increase",
    "rw_friction_degradation",
    "adcs_reaction_wheel_speed_limit",
)
PROPULSION_EFFECTS = (
    "propulsion_thruster_ignition_failure",
    "propulsion_burn_impulse_loss",
)


def _values(group: str) -> dict[str, Any]:
    if group == "propulsion":
        return {
            "initial_soc": 0.9,
            "propulsion_enabled": True,
            "propulsion_burn_start_s": 5.0,
            "propulsion_burn_on_time_s": 8.0,
            "propulsion_thrust_n": 5.0,
            "propulsion_isp_s": 200.0,
            "propulsion_initial_propellant_kg": 1.0,
            "propulsion_tank_capacity_kg": 2.0,
            "propulsion_min_burn_soc": 0.05,
        }
    return {
        "initial_soc": 0.75,
        "rw_initial_speed_rad_s_0": 10.471975511965978,
        "rw_initial_speed_rad_s_1": 20.943951023931955,
        "rw_initial_speed_rad_s_2": 31.41592653589793,
    }


def _spec(
    group: str,
    run_id: str,
    *,
    category: str | None = None,
    effect: str | None = None,
    parameters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    values = _values(group)
    effects = ADCS_EFFECTS if group == "adcs" else PROPULSION_EFFECTS
    evidence_fields: set[str] = set()
    for effect_id in effects:
        profile = get_effect_evidence_profile(WHOLE, effect_id)
        assert profile is not None, effect_id
        evidence_fields.update(profile.evidence_fields)
    qoi = sorted(
        field.replace("{wheel_index}", "*")
        for field in evidence_fields
        if not field.startswith("label.")
    )
    spec: dict[str, Any] = {
        "schema_version": "1.0.0",
        "task": {"id": run_id, "name": "P9-O3 whole-spacecraft event evidence"},
        "model": {
            "capability_id": WHOLE,
            "target": {
                "level": "whole_spacecraft",
                "name": "whole_spacecraft",
                "mode": "mixed" if category == "constraint" else (category or "nominal"),
            },
            "config": dict(values),
            "spacecraft": {"mission": {"template": WHOLE}},
        },
        "simulation": {
            "level": "whole_spacecraft",
            "duration_s": RUN_DURATION_S,
            "step_s": 0.2,
            "sample_s": 1.0,
            "backend": "basilisk",
            "time_base": "simulation_seconds",
            "time_system": "UTC",
        },
        "parameters": {"profile": "demo", "values": dict(values), "overrides": []},
        "events": {"faults": [], "degradations": [], "constraints": []},
        "outputs": {
            "output_root": f"runs/{run_id}",
            "qoi": qoi,
            "plots": [],
            "files": [],
            "include_summary": True,
            "include_trace": True,
            "include_labels": True,
            "include_manifest": True,
        },
        "assurance": {
            "allow_proxy": False,
            "claim_level": "analysis_only",
            "parameter_profile": "demo",
            "fidelity_level": "declared_by_capability",
            "validation_profile": "default",
        },
    }
    if category and effect:
        spec["events"][f"{category}s"] = [{
            "id": f"{effect}_event",
            "effect": effect,
            "target": "whole_spacecraft",
            "start_s": EVENT_START_S,
            "end_s": EVENT_END_S,
            "parameters": dict(parameters or {}),
        }]
    return spec


def _run_case(
    output_root: Path,
    group: str,
    run_id: str,
    *,
    category: str | None = None,
    effect: str | None = None,
    parameters: dict[str, Any] | None = None,
):
    pytest.importorskip("Basilisk", reason="requires official Basilisk 2.11.0")
    assert version("bsk") == "2.11.0"
    spec = _spec(
        group,
        run_id,
        category=category,
        effect=effect,
        parameters=parameters,
    )
    prepared = prepare_run(spec, output_root=output_root / "runs", run_id=run_id)
    executed = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )
    assert executed.run_record.status.value == "SUCCEEDED", executed.run_record.model_dump(mode="json")
    assert executed.run_record.sealed is True
    if executed.validation.result.value != "PASS":
        details = executed.validation.model_dump(mode="json")
        print(json.dumps({
            "checks": details.get("checks", []),
            "injection_evidence": details.get("injection_evidence", []),
            "reason_code": details.get("reason_code"),
            "evidence_missing": details.get("evidence_missing", []),
        }, ensure_ascii=False, indent=2, default=str))
        raise AssertionError(f"whole-spacecraft Run Bundle validation did not PASS: {details.get('reason_code')}")
    integrity = verify_run_bundle(prepared.bundle_root)
    assert integrity["ok"] is True, integrity
    assert executed.summary["runtime_truth_status"] == "instantiated_connected_recorded_unified"
    assert executed.summary["external_or_proxy_module_count"] == (5 if group == "propulsion" else 4)
    assert executed.summary["resource_feedback_closure_status"] == "PASS"
    assert executed.summary["energy_bounds_pass"] is True
    assert executed.summary["data_bounds_pass"] is True
    if category:
        assert len(executed.validation.injection_evidence) == 1
        evidence = executed.validation.injection_evidence[0]
        assert evidence.delivery_result.value == "PASS"
        assert evidence.effect_result.value == "PASS"
        assert "RUNTIME_EFFECT_VERIFIED" in evidence.reason_codes
    return executed, integrity


@pytest.fixture(scope="module")
def nominal_runs(tmp_path_factory):
    root = tmp_path_factory.mktemp("p9o3_whole_nominal")
    adcs, adcs_integrity = _run_case(root, "adcs", "p9o3_whole_adcs_nominal")
    propulsion, propulsion_integrity = _run_case(root, "propulsion", "p9o3_whole_propulsion_nominal")
    assert propulsion.summary["propulsion_burn_observed"] is True
    assert propulsion.summary["propellant_used_kg"] > 0.0
    return {
        "adcs": (adcs, adcs_integrity),
        "propulsion": (propulsion, propulsion_integrity),
    }


def _at(rows, time_s: float) -> dict[str, Any]:
    return min(rows, key=lambda row: abs(float(row["time_s"]) - time_s))


@pytest.mark.parametrize(
    ("group", "category", "effect", "parameters"),
    [
        ("adcs", "fault", "adcs_rw_jamming", {"wheel_index": 0, "brake_torque_nm": 0.2}),
        ("adcs", "fault", "adcs_rw_motor_failure", {"wheel_index": 0, "torque_scale": 0.0}),
        ("adcs", "fault", "gyro_bias_step", {"bias_step_deg_s": [0.5, 0.0, 0.0]}),
        ("adcs", "degradation", "gyro_noise_increase", {"noise_scale": 100.0}),
        ("adcs", "degradation", "rw_friction_degradation", {"wheel_index": 0, "drag_nms": 0.012}),
        ("adcs", "constraint", "adcs_reaction_wheel_speed_limit", {
            "wheel_index": 0, "max_speed_rad_s": 5.0,
        }),
        ("propulsion", "fault", "propulsion_thruster_ignition_failure", {}),
        ("propulsion", "degradation", "propulsion_burn_impulse_loss", {"remaining_impulse_ratio": 0.25}),
    ],
)
def test_whole_spacecraft_events_have_paired_native_basilisk_sealed_run_bundles(
    tmp_path: Path,
    nominal_runs,
    group: str,
    category: str,
    effect: str,
    parameters: dict[str, Any],
) -> None:
    nominal, nominal_integrity = nominal_runs[group]
    injected, injected_integrity = _run_case(
        tmp_path,
        group,
        f"p9o3_whole_{effect}_injected",
        category=category,
        effect=effect,
        parameters=parameters,
    )
    active_label = {
        "fault": "label.fault_active",
        "degradation": "label.degradation_active",
        "constraint": "label.constraint_active",
    }[category]
    active_rows = [
        row for row in injected.trace_rows
        if EVENT_START_S <= float(row["time_s"]) <= EVENT_END_S and row.get(active_label) is True
    ]
    assert active_rows, (effect, injected.trace_rows)
    assert any(effect in str(row.get("event.active_effects", "")) for row in active_rows)

    nominal_active = _at(nominal.trace_rows, 8.0)
    injected_active = _at(injected.trace_rows, 8.0)
    nominal_after = _at(nominal.trace_rows, 12.0)
    injected_after = _at(injected.trace_rows, 12.0)

    if effect == "adcs_rw_jamming":
        assert injected_active["adcs.rw.effective_torque_ratio_0"] == pytest.approx(0.0, abs=1e-12)
        assert nominal_active["adcs.rw.effective_torque_ratio_0"] > 0.0
        assert injected_after["adcs.rw.effective_torque_ratio_0"] == pytest.approx(nominal_after["adcs.rw.effective_torque_ratio_0"])
    elif effect == "adcs_rw_motor_failure":
        assert injected_active["adcs.rw.effective_max_torque_nm_0"] == pytest.approx(0.0, abs=1e-12)
        assert nominal_active["adcs.rw.effective_max_torque_nm_0"] > 0.0
        assert injected_after["adcs.rw.effective_max_torque_nm_0"] == pytest.approx(nominal_after["adcs.rw.effective_max_torque_nm_0"])
    elif effect == "gyro_bias_step":
        assert injected_active["adcs.sensor.gyro_bias_norm_rad_s"] > 0.0
        assert nominal_active["adcs.sensor.gyro_bias_norm_rad_s"] == pytest.approx(0.0, abs=1e-12)
        assert injected_after["adcs.sensor.gyro_bias_norm_rad_s"] == pytest.approx(nominal_after["adcs.sensor.gyro_bias_norm_rad_s"], abs=1e-12)
    elif effect == "gyro_noise_increase":
        assert injected_active["adcs.sensor.gyro_noise_norm_rad_s"] > 0.0
        assert nominal_active["adcs.sensor.gyro_noise_norm_rad_s"] == pytest.approx(0.0, abs=1e-12)
        assert injected_after["adcs.sensor.gyro_noise_norm_rad_s"] == pytest.approx(nominal_after["adcs.sensor.gyro_noise_norm_rad_s"], abs=1e-12)
    elif effect == "rw_friction_degradation":
        assert injected_active["adcs.rw.effective_drag_nms_0"] == pytest.approx(0.012, rel=1e-9)
        assert nominal_active["adcs.rw.effective_drag_nms_0"] == pytest.approx(0.0, abs=1e-12)
        assert injected_after["adcs.rw.effective_drag_nms_0"] == pytest.approx(nominal_after["adcs.rw.effective_drag_nms_0"], abs=1e-12)
    elif effect == "adcs_reaction_wheel_speed_limit":
        assert injected_active["adcs.rw.effective_max_speed_rad_s_0"] == pytest.approx(5.0)
        assert any(
            abs(row["adcs.rw.speed_rad_s_0"]) > 5.0
            and abs(row["adcs.control.applied_torque_nm_0"]) > 0.0
            for row in active_rows
        )
        assert injected_after["adcs.rw.effective_max_speed_rad_s_0"] == pytest.approx(nominal_after["adcs.rw.effective_max_speed_rad_s_0"])
    elif effect == "propulsion_thruster_ignition_failure":
        assert nominal.summary["propellant_used_kg"] > 0.0
        assert injected.summary["propulsion_burn_observed"] is False
        assert injected.summary["max_total_thrust_n"] == pytest.approx(0.0, abs=1e-12)
        assert injected.summary["propellant_used_kg"] == pytest.approx(0.0, abs=1e-12)
        assert injected_active["propulsion.total_thrust_n"] == pytest.approx(0.0, abs=1e-12)
        assert injected_active["propulsion.propellant_used_kg"] == pytest.approx(0.0, abs=1e-12)
        assert nominal_active["propulsion.burn_command_on_time_s"] == pytest.approx(8.0)
        assert injected_active["propulsion.burn_command_on_time_s"] == pytest.approx(0.0, abs=1e-12)
        assert injected_active["propulsion.nominal_burn_command_on_time_s"] == pytest.approx(8.0)
    elif effect == "propulsion_burn_impulse_loss":
        assert nominal.summary["propellant_used_kg"] > 0.0
        assert nominal_active["propulsion.burn_command_on_time_s"] == pytest.approx(8.0)
        assert injected_active["propulsion.burn_command_on_time_s"] == pytest.approx(2.0)
        assert injected_active["propulsion.nominal_burn_command_on_time_s"] == pytest.approx(8.0)
        assert injected_active["propulsion.burn_command_on_time_s"] / nominal_active["propulsion.burn_command_on_time_s"] == pytest.approx(0.25)
        assert 0.0 < injected.summary["propellant_used_kg"] < 0.5 * nominal.summary["propellant_used_kg"]
        nominal_burn_samples = sum(bool(row["propulsion.burn_active"]) for row in nominal.trace_rows)
        injected_burn_samples = sum(bool(row["propulsion.burn_active"]) for row in injected.trace_rows)
        assert 0 < injected_burn_samples < nominal_burn_samples
        assert injected.summary["propulsion_burn_observed"] is True
        assert abs(injected.summary["final_orbit_radius_m"] - nominal.summary["final_orbit_radius_m"]) > 0.1

    report_path = Path("reports/native_whole_spacecraft_events/effect_ab.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "p9-o3.whole-event-ab.v1", "cases": []}
    if report_path.exists():
        report = json.loads(report_path.read_text(encoding="utf-8"))
    direct_fields = (
        [key for key in injected_active if key.startswith("adcs.")]
        if group == "adcs"
        else [
            "propulsion.burn_active",
            "propulsion.burn_command_on_time_s",
            "propulsion.nominal_burn_command_on_time_s",
            "propulsion.total_thrust_n",
            "propulsion.fuel_mass_kg",
            "propulsion.propellant_used_kg",
            "propulsion.electrical_power_w",
            "orbit.radius_m",
        ]
    )
    report["cases"].append({
        "group": group,
        "category": category,
        "effect_id": effect,
        "nominal_run_id": nominal.run_record.run_id,
        "injected_run_id": injected.run_record.run_id,
        "nominal": {
            "status": nominal.run_record.status.value,
            "sealed": nominal.run_record.sealed,
            "validation": nominal.validation.result.value,
            "integrity": nominal_integrity,
        },
        "injected": {
            "status": injected.run_record.status.value,
            "sealed": injected.run_record.sealed,
            "validation": injected.validation.result.value,
            "integrity": injected_integrity,
            "runtime_injection_evidence": [
                item.model_dump(mode="json") for item in injected.validation.injection_evidence
            ],
        },
        "active_time_s": 8.0,
        "direct_recorder_comparison": {
            field: {"nominal": nominal_active.get(field), "injected": injected_active.get(field)}
            for field in direct_fields
        },
        "summary_comparison": {
            "nominal_propellant_used_kg": nominal.summary.get("propellant_used_kg"),
            "injected_propellant_used_kg": injected.summary.get("propellant_used_kg"),
            "nominal_max_total_thrust_n": nominal.summary.get("max_total_thrust_n"),
            "injected_max_total_thrust_n": injected.summary.get("max_total_thrust_n"),
        },
    })
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
