from __future__ import annotations

import json
from pathlib import Path

from sat_sim.run_bundle import execute_prepared_run, prepare_run, request_cancel
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
