from __future__ import annotations

import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, request_cancel, verify_run_bundle
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_compiler import compile_task_spec
from sat_sim.unified_execution import execute_compiled_task


def _small_rw_spec(task_id: str) -> dict:
    spec = instantiate_scenario_template("component_reaction_wheel_nominal", task_id=task_id)
    spec["simulation"].update({"duration_s": 1.0, "step_s": 0.1, "sample_s": 0.2})
    return spec


def test_direct_execution_returns_registered_non_legacy_port_evidence(tmp_path: Path) -> None:
    spec = _small_rw_spec("a2r_direct")
    compiled = compile_task_spec(spec)
    result = execute_compiled_task(compiled, task_spec=spec, output_root=tmp_path / "dataset", write_dataset=False)
    evidence = result.runtime_metadata["unified_execution"]
    assert evidence["legacy_mode"] is False
    assert evidence["adapter_key"] == "capability.registered_adapter"
    assert len(evidence["execution_plan_sha256"]) == 64
    assert result.summary


def test_run_bundle_persists_unified_execution_evidence(tmp_path: Path) -> None:
    spec = _small_rw_spec("a2r_bundle")
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=False,
    )
    assert result.run_record.status.value == "SUCCEEDED"
    root = Path(result.bundle_root)
    evidence = json.loads((root / "runtime" / "execution_port.json").read_text(encoding="utf-8"))
    assert evidence["execution_plan_sha256"] == prepared.execution_plan_sha256
    assert evidence["adapter_key"] == "capability.registered_adapter"
    assert evidence["legacy_mode"] is False
    attempt = json.loads((root / "attempts" / "attempt_001" / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["artifacts"]["execution_port"] == "execution_port.json"


def test_failed_attempt_persists_execution_port_evidence(tmp_path: Path) -> None:
    spec = _small_rw_spec("a2r_failed")
    prepared = prepare_run(spec, output_root=tmp_path / "runs")

    def fail_executor(*_args, **_kwargs):
        raise RuntimeError("deterministic test failure")

    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=False,
        executor=fail_executor,
    )
    assert result.run_record.status.value == "FAILED"
    root = Path(result.bundle_root)
    evidence = json.loads((root / "attempts" / "attempt_001" / "execution_port.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "FAILED"
    assert evidence["adapter_key"] == "test.executor"
    assert evidence["failure_reason_code"] == "EXECUTION_FAILED"
    attempt = json.loads((root / "attempts" / "attempt_001" / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["artifacts"]["execution_port"] == "execution_port.json"
    root_evidence = json.loads((root / "runtime" / "execution_port.json").read_text(encoding="utf-8"))
    assert root_evidence["status"] == "FAILED"


def test_pre_execution_cancel_persists_execution_port_evidence(tmp_path: Path) -> None:
    spec = _small_rw_spec("a2r_cancelled")
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    request_cancel(prepared.bundle_root, reason="contract-test")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=False,
    )
    assert result.run_record.status.value == "CANCELLED"
    root = Path(result.bundle_root)
    evidence = json.loads((root / "attempts" / "attempt_001" / "execution_port.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "CANCELLED_BEFORE_EXECUTION"
    assert evidence["adapter_key"] == "capability.registered_adapter"
    attempt = json.loads((root / "attempts" / "attempt_001" / "attempt.json").read_text(encoding="utf-8"))
    assert attempt["artifacts"]["execution_port"] == "execution_port.json"
    root_evidence = json.loads((root / "runtime" / "execution_port.json").read_text(encoding="utf-8"))
    assert root_evidence["status"] == "CANCELLED_BEFORE_EXECUTION"


def test_native_reaction_wheel_component_survives_full_sealed_run_bundle(tmp_path: Path) -> None:
    """P9-N1: official Basilisk RW must execute through TaskSpec and sealed Run Bundle."""
    import pytest
    pytest.importorskip("Basilisk")

    spec = _small_rw_spec("p9n_native_rw_run_bundle")
    spec["simulation"].update({"duration_s": 2.0, "step_s": 0.1, "sample_s": 0.1})
    spec["parameters"]["values"].update({
        "physics_backend": "basilisk_native",
        "num_wheels": 1,
        "wheel_inertia_kg_m2": 0.1,
        "max_motor_torque_nm": 0.005,
        "max_speed_rad_s": 1000.0,
        "initial_wheel_speeds_rad_s": 0.0,
        "command_torque_nm": 0.05,
        "damping_nms": 0.0,
    })
    # A component run must not inherit spacecraft-wide checks that require
    # orbit, attitude, EPS or thermal fields. Apply explicit component QoI
    # assertions instead; the native recorder bounds are checked below.
    spec.setdefault("model", {}).setdefault("validation", {}).update({
        "physical_checks": [],
        "acceptance_assertions": [{
            "id": "native_rw_speed_response",
            "metric": "qoi.adcs.reaction_wheel.final_speed_rad_s_0",
            "operator": ">",
            "value": 0.02,
        }],
    })
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )

    assert result.run_record.status.value == "SUCCEEDED"
    assert result.validation.result.value == "PASS"
    assert result.run_record.sealed is True
    assert result.bundle_manifest
    assert verify_run_bundle(result.bundle_root)["ok"] is True
    assert result.summary["physics_backend"] == "basilisk_native"
    assert result.summary["runtime_truth_status"] == "real_native_rw_spacecraft_recorded"
    assert result.summary["adapter_metadata"]["native_provenance"] == "Basilisk.simulation.reactionWheelStateEffector"
    assert result.summary["target_level"] == "component"
    assert result.summary["target_name"] == "reaction_wheel"
    assert len(result.trace_rows) > 5
    speeds = [float(row["adcs.reaction_wheel.speed_rad_s_0"]) for row in result.trace_rows]
    applied = [float(row["adcs.reaction_wheel.applied_torque_nm_0"]) for row in result.trace_rows]
    assert abs(speeds[-1] - speeds[0]) > 0.02
    assert max(abs(value) for value in applied) == pytest.approx(0.005, abs=1e-5)
    assert all(abs(value) <= 0.005 + 1e-8 for value in applied)

    root = Path(result.bundle_root)
    evidence = json.loads((root / "runtime" / "execution_port.json").read_text(encoding="utf-8"))
    assert evidence["route"] == "registered_capability_adapter"
    assert evidence["legacy_mode"] is False
    assert evidence["capability_id"] == "component.reaction_wheel.v1"
    assert evidence["capability_adapter"] == "sat_sim.adapters.component_reaction_wheel.ReactionWheelAdapter"
    assert evidence["execution_plan_sha256"] == prepared.execution_plan_sha256
    assert evidence["request_sha256"]
    assert result.bundle_manifest["integrity"]["ok"] is True
