from __future__ import annotations

import inspect
import json
import os
from pathlib import Path

import pytest

from sat_sim.agent_facade import (
    AgentFacadeRequest,
    _apply_request_semantics,
    _canonical_provenance,
    _effect_intent_state,
    _sanitize_generated_draft,
)
from sat_sim.api import ExperimentLaunchBody, RunCreateBody, RunExecuteBody, TaskCenterRunBody
from sat_sim.capability_planner import _detect_unsupported
from sat_sim.coupling_causality import CausalExpectation, evaluate_coupling_causality
from sat_sim.durable_queue import DurableExecutionQueue
from sat_sim.run_bundle import (
    AttemptStatus,
    ExecutionAttempt,
    RunRecord,
    RunStatus,
    _execution_lock,
    execute_prepared_run,
    recover_interrupted_run,
)
import yaml
from whole_spacecraft.runner import _structure_for_run_config
from whole_spacecraft.schemas import WholeSpacecraftConfig, WholeSpacecraftRunConfig


def _draft_with_degradation() -> dict:
    return {
        "schema_version": "1.0",
        "task": {"id": "agent_event_test"},
        "simulation": {"level": "component", "duration_s": 600.0},
        "model": {"capability_id": "component.reaction_wheel.v1"},
        "parameters": {"values": {}},
        "events": {
            "faults": [],
            "degradations": [{"type": "coulomb_friction_increase", "parameters": {"value": 0.1}}],
            "constraints": [{"type": "runtime_limit", "parameters": {"duration_s": 600.0}}],
        },
        "metadata": {},
    }


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("300秒时让1号轮增加库仑摩擦", "explicit_positive"),
        ("电池容量从160Wh降到120Wh", "explicit_positive"),
        ("发射机功率逐步减半", "explicit_positive"),
        ("不添加任何故障或退化", "explicit_negative"),
        ("把运行时间限制为300秒", "unconfirmed"),
    ],
)
def test_effect_intent_is_tri_state_and_preserves_engineering_language(text: str, expected: str) -> None:
    assert _effect_intent_state(text) == expected


def test_sanitizer_preserves_explicit_degradation_and_only_removes_on_explicit_negative() -> None:
    positive, _ = _sanitize_generated_draft(_draft_with_degradation(), "让反作用轮增加库仑摩擦")
    assert positive["events"]["degradations"]

    negative, _ = _sanitize_generated_draft(_draft_with_degradation(), "不添加故障或退化")
    assert negative["events"]["degradations"] == []
    assert negative["events"]["constraints"]  # runtime constraints are not fault events

    unconfirmed, _ = _sanitize_generated_draft(_draft_with_degradation(), "建立反作用轮仿真")
    assert unconfirmed["events"]["degradations"]
    assert unconfirmed["metadata"]["agent_safety"]["generated_events_require_confirmation"] is True


def test_normal_structure_wording_does_not_trigger_high_fidelity_structure_rejection() -> None:
    capability_id = "whole_spacecraft.composite_digital_twin.v1"
    assert _detect_unsupported("建立整星仿真并输出结果目录结构", capability_id) == ()
    assert _detect_unsupported("建立整星仿真，输出结构化结果", capability_id) == ()
    assert _detect_unsupported("建立整星柔性结构动力学有限元仿真", capability_id)


def test_rpm_repair_only_derives_torque_when_target_semantics_are_explicit() -> None:
    base = {
        "schema_version": "1.0",
        "task": {"id": "rw"},
        "simulation": {"level": "component", "duration_s": 60.0},
        "model": {"capability_id": "component.reaction_wheel.v1"},
        "parameters": {"values": {"wheel_inertia_kg_m2": 0.1, "max_motor_torque_nm": 0.02}},
        "events": {"faults": [], "degradations": [], "constraints": []},
        "metadata": {},
    }
    ambiguous = json.loads(json.dumps(base))
    repairs = _apply_request_semantics(ambiguous, "两个轮速分别为1000 rpm和2000 rpm")
    assert not any(item["action"] == "derived_from_explicit_target_speed" for item in repairs)
    assert ambiguous["metadata"]["agent_safety"]["requires_confirmation"] is True

    explicit = json.loads(json.dumps(base))
    repairs = _apply_request_semantics(explicit, "轮速从1000 rpm加速到2000 rpm")
    assert any(item["action"] == "derived_from_explicit_target_speed" for item in repairs)
    assert "command_torque_nm" in explicit["parameters"]["values"]


def test_provenance_distinguishes_user_explicit_from_agent_default_and_requires_confirmation() -> None:
    spec = yaml.safe_load(Path("examples/whole_spacecraft_composite_digital_twin_nominal.yaml").read_text(encoding="utf-8"))
    spec["task_id"] = "v0571_provenance"
    spec["parameters"]["battery_capacity_wh"] = 200.0
    result = _canonical_provenance(
        spec,
        AgentFacadeRequest(request="建立整星仿真，电池容量200Wh", backend="openai_compatible"),
    )
    fields = result["provenance"]["fields"]
    assert fields["parameters.values.battery_capacity_wh"]["source"] == "user_explicit"
    assert fields["parameters.values.initial_soc"]["source"] == "agent_inferred"
    assert result["metadata"]["agent"]["requires_confirmation"] is True
    assert any("initial_soc" in item for item in result["metadata"]["agent"]["confirmation_reasons"])


def test_production_execution_defaults_to_hard_timeout_and_two_attempts(tmp_path: Path) -> None:
    assert inspect.signature(execute_prepared_run).parameters["hard_timeout"].default is True
    assert RunCreateBody(task_spec={}).hard_timeout is True
    assert RunExecuteBody(execution_plan_sha256="a" * 64).hard_timeout is True
    assert TaskCenterRunBody().hard_timeout is True
    assert ExperimentLaunchBody().hard_timeout is True
    assert RunCreateBody(task_spec={}).max_attempts == 2

    queue = DurableExecutionQueue(tmp_path / "queue.sqlite3")
    job = queue.enqueue(
        run_id="run-defaults",
        bundle_root=tmp_path / "bundle",
        execution_plan_sha256="b" * 64,
    )
    assert job.hard_timeout is True
    assert job.max_attempts == 2


def test_interrupted_run_recovery_preserves_aborted_attempt_and_resets_bundle(tmp_path: Path) -> None:
    root = tmp_path / "run"
    (root / "attempts" / "attempt_001").mkdir(parents=True)
    (root / "runtime").mkdir(parents=True)
    (root / "control").mkdir(parents=True)
    attempt = ExecutionAttempt(
        attempt_id="attempt_001",
        sequence=1,
        status=AttemptStatus.RUNNING,
        started_at="2026-07-19T00:00:00Z",
        plan_sha256="c" * 64,
    )
    (root / "attempts" / "attempt_001" / "attempt.json").write_text(
        json.dumps(attempt.model_dump(mode="json")), encoding="utf-8"
    )
    record = RunRecord(
        run_id="run-recovery",
        task_id="task-recovery",
        status=RunStatus.RUNNING,
        created_at="2026-07-19T00:00:00Z",
        updated_at="2026-07-19T00:00:00Z",
        canonical_task_spec_sha256="d" * 64,
        resolved_spec_sha256="e" * 64,
        execution_plan_sha256="c" * 64,
        attempts=[attempt],
    )
    (root / "run_record.json").write_text(json.dumps(record.model_dump(mode="json")), encoding="utf-8")
    (root / "runtime" / "execution.lock").write_text("pid=99999999\nhost=localhost.invalid\n", encoding="utf-8")

    recovered = recover_interrupted_run(root, reason="test worker crash")
    assert recovered.status == RunStatus.PREPARED
    assert recovered.attempts[0].status == AttemptStatus.ABORTED
    assert recovered.attempts[0].failure is not None
    assert (root / "runtime" / "interrupted_run_recovery.json").is_file()

    with _execution_lock(root):
        assert (root / "runtime" / "execution.lock").is_file()
    assert not (root / "runtime" / "execution.lock").exists()


def test_output_sample_period_does_not_change_thermal_or_orbit_period() -> None:
    structure = WholeSpacecraftConfig(
        adcs_dyn_step_s=0.2,
        adcs_fsw_step_s=0.2,
        orb_env_step_s=1.0,
        thermal_step_s=10.0,
        recorder_step_s=10.0,
    )
    resolved = _structure_for_run_config(WholeSpacecraftRunConfig(sample_s=0.4, structure=structure))
    assert resolved.recorder_step_s == 0.4
    assert resolved.thermal_step_s == 10.0
    assert resolved.orb_env_step_s == 1.0
    assert resolved.adcs_dyn_step_s == 0.2


def test_whole_spacecraft_builder_defines_separate_multirate_tasks() -> None:
    source = Path("src/whole_spacecraft/builder.py").read_text(encoding="utf-8")
    assert 'wholeSpacecraftOrbitEnvironmentTask' in source
    assert 'wholeSpacecraftThermalTask' in source
    assert 'wholeSpacecraftRecorderTask' in source
    assert 'attach_basilisk_orbital_environment_to_task(sim, orb_env_task_name' in source
    assert 'attach_thermal_network_graph_to_task(sim, thermal_task_name' in source
    assert 'AddModelToTask(recorder_task_name' in source
    assert 'CreateNewTask(thermal_task_name, macros.sec2nano(float(cfg.thermal_step_s)))' in source


def test_paired_run_causality_requires_source_change_response_direction_and_lag() -> None:
    baseline = [
        {"time_s": 0.0, "source": 1.0, "sink": 10.0},
        {"time_s": 1.0, "source": 1.0, "sink": 10.0},
        {"time_s": 2.0, "source": 1.0, "sink": 10.0},
    ]
    perturbed = [
        {"time_s": 0.0, "source": 1.0, "sink": 10.0},
        {"time_s": 1.0, "source": 2.0, "sink": 10.0},
        {"time_s": 2.0, "source": 2.0, "sink": 12.0},
    ]
    report = evaluate_coupling_causality(
        baseline,
        perturbed,
        [CausalExpectation(
            coupling_id="source_to_sink",
            source_field="source",
            response_field="sink",
            intervention_time_s=1.0,
            expected_direction="increase",
            min_source_delta=0.5,
            min_response_delta=1.0,
            max_lag_s=1.0,
        )],
    )
    assert report["status"] == "PASS"
    assert report["checks"][0]["first_response_lag_s"] == 1.0

    wrong_direction = evaluate_coupling_causality(
        baseline,
        [{**row, "source": 2.0 if row["time_s"] >= 1 else 1.0, "sink": 8.0 if row["time_s"] >= 2 else 10.0} for row in baseline],
        [CausalExpectation(
            coupling_id="source_to_sink",
            source_field="source",
            response_field="sink",
            intervention_time_s=1.0,
            expected_direction="increase",
            min_source_delta=0.5,
            min_response_delta=1.0,
        )],
    )
    assert wrong_direction["status"] == "FAIL"
    assert "SINK_RESPONSE_DIRECTION_MISMATCH" in wrong_direction["checks"][0]["reason_codes"]


def test_cross_domain_adcs_to_comm_gate_routes_consistently_to_composite() -> None:
    from sat_sim.capability_planner import plan_capability_for_request

    requests = (
        "整星统一运行图，验证姿态误差影响下行通信",
        "完整整星全分系统仿真，验证姿态误差影响下行通信",
    )
    for text in requests:
        result = plan_capability_for_request(text)
        assert result.supported is True
        assert result.selected_capability_id == "whole_spacecraft.composite_digital_twin.v1"


def test_parameter_effect_audit_requires_measurable_expected_response() -> None:
    from sat_sim.coupling_causality import ParameterEffectExpectation, evaluate_parameter_effects

    baseline = [
        {"time_s": 0.0, "eps.battery_soc": 0.9},
        {"time_s": 1.0, "eps.battery_soc": 0.8},
        {"time_s": 2.0, "eps.battery_soc": 0.7},
    ]
    perturbed = [
        {"time_s": 0.0, "eps.battery_soc": 0.9},
        {"time_s": 1.0, "eps.battery_soc": 0.75},
        {"time_s": 2.0, "eps.battery_soc": 0.60},
    ]
    report = evaluate_parameter_effects(
        baseline,
        perturbed,
        [ParameterEffectExpectation(
            parameter_path="parameters.values.battery_capacity_wh",
            baseline_value=160.0,
            perturbed_value=120.0,
            response_field="eps.battery_soc",
            expected_direction="decrease",
            min_response_delta=0.05,
            intervention_time_s=1.0,
        )],
    )
    assert report["status"] == "PASS"
    assert report["checks"][0]["observed_direction"] == "decrease"

    no_effect = evaluate_parameter_effects(
        baseline,
        baseline,
        [ParameterEffectExpectation(
            parameter_path="parameters.values.battery_capacity_wh",
            baseline_value=160.0,
            perturbed_value=120.0,
            response_field="eps.battery_soc",
            expected_direction="decrease",
            min_response_delta=0.05,
        )],
    )
    assert no_effect["status"] == "FAIL"
    assert "PARAMETER_RESPONSE_NOT_OBSERVED" in no_effect["checks"][0]["reason_codes"]


def test_single_run_physics_is_not_labelled_as_full_causal_pass() -> None:
    runner_source = Path("src/whole_spacecraft/runner.py").read_text(encoding="utf-8")
    unified_source = Path("src/sat_sim/bsk_engine/unified_native.py").read_text(encoding="utf-8")
    assert 'physics_status = "PASS_SINGLE_RUN_LIMITED"' in runner_source
    assert 'coupling_causality_status="NOT_EVALUATED_PAIRED_RUN_REQUIRED"' in runner_source
    assert '"status": "OBSERVED_SINGLE_RUN" if observed else "NOT_EXERCISED"' in unified_source
    assert '"paired_run_required_for_causal_pass": True' in unified_source
