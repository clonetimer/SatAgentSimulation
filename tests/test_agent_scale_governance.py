from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from sat_sim.agent_facade import AgentFacadeRequest, AgentFacadeStep, _backend_identity, _canonical_provenance
from sat_sim.golden_eval import GOLDEN_SET_VERSION, run_golden_evals
from sat_sim.llm_backends import LLMCallResult
from sat_sim.parameter_consumption import audit_parameter_consumption
from sat_sim.run_bundle import execute_prepared_run, prepare_run
from sat_sim.scenario_templates import instantiate_scenario_template
from sat_sim.task_models import canonicalize_task_spec
from sat_sim.task_validator import validate_task_spec


def test_model_invocation_evidence_is_hash_backed_and_structured() -> None:
    prompt = "create a reaction wheel TaskSpec"
    raw = '{"schema_version":"1.0","task":{"id":"x"}}'
    call = LLMCallResult(
        ok=True,
        backend="openai_compatible",
        raw_text=raw,
        prompt_chars=len(prompt),
        metadata={
            "model": "qwen-test",
            "response_id": "resp-123",
            "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
            "usage": {"prompt_tokens": 10, "completion_tokens": 8},
            "latency_ms": 12.5,
        },
    )
    evidence = call.invocation_evidence()
    assert evidence["verified"] is True
    assert evidence["request_id"] == "resp-123"
    assert evidence["prompt_sha256"] == hashlib.sha256(prompt.encode()).hexdigest()
    assert evidence["response_sha256"] == hashlib.sha256(raw.encode()).hexdigest()
    assert evidence["structured_output_kind"] == "json"
    assert evidence["structured_output_sha256"]


def test_backend_identity_requires_invocation_evidence_not_successful_step_name() -> None:
    request = AgentFacadeRequest(request="demo", backend="openai_compatible", model_name="qwen-test")
    unverified = _backend_identity(
        request,
        steps=(AgentFacadeStep("backend.model_generate", "complete", "draft returned"),),
    )
    assert unverified["actual_model_execution_verified"] is False
    assert unverified["actual_model_used"] is None

    evidence = {
        "verified": True,
        "model_id": "qwen-test",
        "request_id": "resp-1",
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
    }
    verified = _backend_identity(
        request,
        steps=(AgentFacadeStep("backend.model_invocation", "complete", payload=evidence),),
    )
    assert verified["actual_model_execution_verified"] is True
    assert verified["actual_model_used"] is True
    assert verified["invocation_evidence"] == evidence


def test_golden_eval_records_dag_state_and_typed_mutations(tmp_path: Path) -> None:
    manifest = {
        "schema_version": GOLDEN_SET_VERSION,
        "cases": [
            {
                "case_id": "g5_component_battery",
                "category": "component_scenarios",
                "input_kind": "form",
                "input": {
                    "form": {
                        "task_id": "g5_component_battery",
                        "capability_id": "component.battery.v1",
                        "level": "component",
                        "duration_s": 60.0,
                        "sample_s": 10.0,
                        "outputs": {"qoi": ["qoi.eps.battery.initial_soc"]},
                    }
                },
                "expected": {
                    "accept": True,
                    "capability_id": "component.battery.v1",
                    "level": "component",
                    "qoi": ["qoi.eps.battery.initial_soc"],
                },
            }
        ],
    }
    manifest_path = tmp_path / "golden_set.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = run_golden_evals(manifest_path, output_dir=tmp_path / "eval", repeat_count=1)
    repetition = report.cases[0].repetitions[0]
    dag_state = json.loads((tmp_path / "eval" / "dag_state_report.json").read_text(encoding="utf-8"))
    mutations = (tmp_path / "eval" / "agent_mutations.jsonl").read_text(encoding="utf-8").splitlines()

    assert report.ok is True
    assert report.metrics["dag_state_hash_availability_rate"] == 1.0
    assert report.metrics["typed_mutation_log_rate"] == 1.0
    assert report.metrics["arbitrary_python_generated_rate"] == 0.0
    assert repetition.dag_state_hash
    assert repetition.dag_mutation_count >= 10
    assert dag_state["ok"] is True
    assert mutations
    assert all(json.loads(line)["schema_version"] == "sat-sim.dag-mutation.v1" for line in mutations)


def test_parameter_consumption_blocks_unknown_parameter_and_canonical_value_wins_alias() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0566_parameter_audit")
    changed = deepcopy(spec)
    changed["parameters"]["values"]["payload_power_w"] = 123.0
    changed["model"]["config"]["payload_power_w"] = 38.0
    audit = audit_parameter_consumption(changed)
    assert audit.ok is True
    assert "model.config.payload_power_w" in audit.alias_paths
    assert "model.config.payload_power_w" in audit.details["shadowed_compatibility_aliases"]

    changed["parameters"]["values"]["payload_power_typo_w"] = 10.0
    audit = audit_parameter_consumption(changed)
    assert audit.ok is False
    assert "parameters.values.payload_power_typo_w" in audit.unknown_paths
    validation = validate_task_spec(changed)
    assert any(item.code == "PARAMETER_PATH_UNCONSUMED" for item in validation.errors)


def test_agent_provenance_records_parameter_and_event_parameter_paths() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native_fault_chain", task_id="v0566_provenance")
    enriched = _canonical_provenance(
        spec,
        AgentFacadeRequest(request="create a fault case", backend="openai_compatible"),
    )
    fields = enriched["provenance"]["fields"]
    assert fields["parameters.values.initial_soc"]["source"] == "agent_inferred"
    assert any(path.startswith("events.faults[") and ".parameters." in path for path in fields)


def test_native_capability_accepts_stream_faster_than_global_sample_when_step_aligned() -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0566_native_high_rate")
    spec["simulation"].update({"duration_s": 4.0, "step_s": 0.2, "sample_s": 1.0})
    spec["outputs"]["telemetry_streams"] = [
        {"stream_id": "fast_adcs", "sample_s": 0.4, "fields": ["adcs.pointing_error_deg"], "format": "csv"}
    ]
    result = validate_task_spec(spec)
    assert result.ok, result.to_dict()


def test_non_native_capability_rejects_stream_faster_than_global_sample() -> None:
    spec = instantiate_scenario_template("component_reaction_wheel_nominal", task_id="v0566_non_native_high_rate")
    spec["simulation"].update({"duration_s": 4.0, "step_s": 0.2, "sample_s": 1.0})
    spec["outputs"]["telemetry_streams"] = [
        {"stream_id": "fast_rw", "sample_s": 0.4, "fields": ["adcs.reaction_wheel.speed_rad_s_0"], "format": "csv"}
    ]
    result = validate_task_spec(spec)
    assert not result.ok
    assert any(item.code == "NATIVE_HIGH_RATE_TELEMETRY_UNSUPPORTED" for item in result.errors)


def test_parameter_audit_is_persisted_through_resolved_spec_plan_and_run_bundle(tmp_path: Path) -> None:
    spec = instantiate_scenario_template("component_reaction_wheel_nominal", task_id="v0566_audit_bundle")
    spec["simulation"].update({"duration_s": 1.0, "step_s": 0.1, "sample_s": 0.2})
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    bundle = Path(prepared.bundle_root)
    audit = json.loads((bundle / "input" / "parameter_consumption.json").read_text(encoding="utf-8"))
    resolved = json.loads((bundle / "input" / "resolved_spec.json").read_text(encoding="utf-8"))
    plan = json.loads((bundle / "input" / "execution_plan.json").read_text(encoding="utf-8"))
    assert audit["ok"] is True
    assert resolved["parameter_consumption"] == audit
    assert any(node["node_id"] == "validate_parameter_consumption" for node in plan["nodes"])


def test_hard_timeout_transport_returns_file_verified_result(tmp_path: Path) -> None:
    spec = instantiate_scenario_template("component_reaction_wheel_nominal", task_id="v0566_timeout_transport")
    spec["simulation"].update({"duration_s": 1.0, "step_s": 0.1, "sample_s": 0.2})
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    result = execute_prepared_run(
        prepared.bundle_root,
        expected_plan_sha256=prepared.execution_plan_sha256,
        max_attempts=1,
        hard_timeout=True,
    )
    assert result.run_record.status.value == "SUCCEEDED"
    trace = json.loads((Path(result.bundle_root) / "runtime" / "execution_trace.json").read_text(encoding="utf-8"))
    assert any(item["node_id"] == "validate_parameter_consumption" for item in trace["events"])


def test_native_high_rate_recorder_is_real_and_not_interpolated(tmp_path: Path) -> None:
    spec = instantiate_scenario_template("whole_spacecraft_unified_native", task_id="v0566_native_high_rate_run")
    spec["simulation"].update({"duration_s": 2.0, "step_s": 0.2, "sample_s": 1.0})
    spec["outputs"]["telemetry_streams"] = [
        {"stream_id": "fast_adcs", "sample_s": 0.4, "fields": ["adcs.pointing_error_deg"], "format": "csv"}
    ]
    prepared = prepare_run(spec, output_root=tmp_path / "runs")
    result = execute_prepared_run(
        prepared.bundle_root, expected_plan_sha256=prepared.execution_plan_sha256, max_attempts=1
    )
    manifest = json.loads(
        (Path(result.bundle_root) / "results" / "dataset" / "telemetry" / "multi_rate_manifest.json").read_text(encoding="utf-8")
    )
    assert manifest["recorder_mode"] == "basilisk_message_recorder_groups"
    assert manifest["interpolation_used"] is False
    assert manifest["post_run_downsampling"] is False
    stream = manifest["streams"][0]
    assert stream["sample_s"] == 0.4
    assert stream["row_count"] == 6
    assert stream["source_base_sample_s"] == 0.4


def test_release_degradation_event_parameter_contract_is_consumed() -> None:
    from sat_sim.release_closure import representative_release_forms
    from sat_sim.unified_agent import UnifiedAgentRequest, run_unified_agent

    case = next(item for item in representative_release_forms() if item["case_id"] == "component_degradation")
    agent = run_unified_agent(UnifiedAgentRequest(input_kind="form", form_data=case["form"]))
    assert agent.task_spec is not None
    audit = audit_parameter_consumption(agent.task_spec)
    assert audit.ok is True
    assert not audit.unknown_paths
    assert any(path.endswith("parameters.capacity_loss_pct") for path in audit.consumed_paths)
