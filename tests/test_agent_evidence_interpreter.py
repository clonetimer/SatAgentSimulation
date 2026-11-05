from __future__ import annotations

import json

import pytest

from sat_sim.evidence_interpreter import (
    EvidenceInterpretation,
    InterpretationValidationError,
    interpret_evidence,
    validate_interpretation,
)
from sat_sim.llm_backends import LLMCallResult


def _evidence(*, ready=True):
    return {
        "schema_version": "agent-experiment-evidence.v1",
        "experiment_id": "exp_demo",
        "evidence_ready": ready,
        "runs": [
            {
                "run_id": "exp_demo_v001",
                "metrics": {"final_soc": 0.72, "load_shed_event_count": 1},
            },
            {
                "run_id": "exp_demo_v002",
                "metrics": {"final_soc": 0.80, "load_shed_event_count": 0},
            },
        ],
        "comparisons": [
            {
                "baseline_run_id": "exp_demo_v001",
                "run_id": "exp_demo_v002",
                "metric_deltas": {
                    "final_soc": 0.08,
                    "load_shed_event_count": -1.0,
                },
            }
        ],
        "warnings": [] if ready else [{"code": "METRICS_NOT_AVAILABLE"}],
        "claim_policy": {
            "causal_explanation": "Not verified by this evidence package alone.",
        },
    }


class FakeBackend:
    name = "fake"

    def __init__(self, payload):
        self.payload = payload
        self.prompts = []

    def generate_text(self, prompt):
        self.prompts.append(prompt)
        return LLMCallResult(
            ok=True,
            backend=self.name,
            raw_text=json.dumps(self.payload, ensure_ascii=False),
            prompt_chars=len(prompt),
            metadata={"model": "fake-model"},
        )


def _valid_payload():
    return {
        "schema_version": "agent-evidence-interpretation.v1",
        "analysis_status": "EVIDENCE_READY",
        "observations": [
            {
                "statement": "候选运行提供了可引用的电源结果。",
                "evidence": [{"run_id": "exp_demo_v002", "metric": "final_soc"}],
            }
        ],
        "comparisons": [
            {
                "statement": "该指标相对基线存在已计算差异。",
                "baseline_run_id": "exp_demo_v001",
                "run_id": "exp_demo_v002",
                "metric": "final_soc",
                "delta": 0.08,
            }
        ],
        "hypotheses": [
            {
                "statement": "容量变化可能与末端状态差异有关。",
                "evidence": [
                    {"run_id": "exp_demo_v001", "metric": "final_soc"},
                    {"run_id": "exp_demo_v002", "metric": "final_soc"},
                ],
                "limitation": "当前对比本身不能证明因果关系。",
                "confidence": "medium",
            }
        ],
        "next_experiments": [
            {
                "objective": "进一步区分容量变化与其他条件的影响。",
                "parameter_path": "parameters.values.battery_capacity_wh",
                "values": [140, 180],
                "rationale": "保持其他条件不变并增加中间候选。",
                "estimated_runs": 2,
            }
        ],
        "unresolved": [],
    }


def test_valid_model_output_is_grounded_and_next_experiment_is_registered():
    backend = FakeBackend(_valid_payload())
    result = interpret_evidence(
        question="比较结果并给出下一步建议",
        evidence=_evidence(),
        allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
        backend=backend,
    )
    assert result["ok"] is True
    assert result["mode"] == "model"
    assert result["interpretation"]["comparisons"][0]["delta"] == pytest.approx(0.08)
    assert result["interpretation"]["next_experiments"][0]["estimated_runs"] == 2
    assert "EVIDENCE_JSON" in backend.prompts[0]


def test_unknown_run_reference_is_rejected():
    payload = _valid_payload()
    payload["observations"][0]["evidence"][0]["run_id"] = "invented_run"
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=_evidence(),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            backend=FakeBackend(payload),
        )
    assert captured.value.reason_code == "UNKNOWN_RUN_REFERENCE"


def test_fabricated_metric_delta_is_rejected():
    payload = _valid_payload()
    payload["comparisons"][0]["delta"] = 99.0
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=_evidence(),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            backend=FakeBackend(payload),
        )
    assert captured.value.reason_code == "COMPARISON_DELTA_MISMATCH"


def test_unregistered_next_experiment_parameter_is_rejected():
    payload = _valid_payload()
    payload["next_experiments"][0]["parameter_path"] = "parameters.values.invented"
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=_evidence(),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            backend=FakeBackend(payload),
        )
    assert captured.value.reason_code == "UNREGISTERED_EXPERIMENT_PARAMETER"


def test_numeric_claim_in_free_text_is_rejected():
    payload = _valid_payload()
    payload["observations"][0]["statement"] = "末端状态提高了百分之 8。"
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=_evidence(),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            backend=FakeBackend(payload),
        )
    assert captured.value.reason_code == "UNSTRUCTURED_NUMERIC_CLAIM"


def test_analysis_status_cannot_upgrade_incomplete_evidence():
    payload = _valid_payload()
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=_evidence(ready=False),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            backend=FakeBackend(payload),
        )
    assert captured.value.reason_code == "INTERPRETATION_STATUS_MISMATCH"


def test_deterministic_mode_never_invents_hypotheses_or_next_experiments():
    result = interpret_evidence(question="解释", evidence=_evidence())
    assert result["mode"] == "deterministic"
    interpretation = result["interpretation"]
    assert interpretation["observations"]
    assert interpretation["comparisons"]
    assert interpretation["hypotheses"] == []
    assert interpretation["next_experiments"] == []


def test_schema_forbids_high_confidence_causal_hypothesis():
    payload = _valid_payload()
    payload["hypotheses"][0]["confidence"] = "high"
    with pytest.raises(Exception):
        EvidenceInterpretation.model_validate(payload)


def test_validator_accepts_exact_registered_comparison():
    interpretation = EvidenceInterpretation.model_validate(_valid_payload())
    result = validate_interpretation(
        interpretation,
        evidence=_evidence(),
        allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
    )
    assert result.comparisons[0].metric == "final_soc"


def test_interpret_api_deterministic_mode_does_not_call_model(tmp_path):
    from fastapi.testclient import TestClient

    from sat_sim.api import create_app
    from sat_sim.scenario_templates import instantiate_scenario_template

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
    )
    client = TestClient(app)
    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "interpret-api:create",
            "name": "interpret api",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "sweep": {"simulation.duration_s": [10]},
        },
    )
    experiment_id = created.json()["experiment_id"]
    response = client.post(
        f"/agent/experiments/{experiment_id}/interpret",
        json={"question": "总结现有证据", "mode": "deterministic"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["analysis"]["mode"] == "deterministic"
    assert payload["analysis"]["model_call"] is None
    assert payload["analysis"]["interpretation"]["analysis_status"] == "INSUFFICIENT_EVIDENCE"
    assert payload["next_experiments_are_proposals_only"] is True


def test_interpret_api_model_output_is_still_validated(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import sat_sim.api as api_module
    import sat_sim.evidence_interpreter as interpreter_module
    from sat_sim.api import create_app
    from sat_sim.scenario_templates import instantiate_scenario_template

    evidence = _evidence()
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: evidence)
    provider_path = tmp_path / "providers.json"
    provider_path.write_text(json.dumps({
        "providers": [{
            "provider_id": "test-evidence-provider",
            "label": "test",
            "backend": "openai_compatible",
            "location": "local",
            "tier": "L2",
            "model": "fake-model",
            "base_url": "http://127.0.0.1:9999/v1",
            "enabled": True,
        }]
    }), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_MODEL_PROVIDERS_FILE", str(provider_path))

    invalid = _valid_payload()
    invalid["comparisons"][0]["delta"] = 7.0
    backend = FakeBackend(invalid)
    monkeypatch.setattr(interpreter_module, "make_text_llm_backend", lambda _config: backend)

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
    )
    client = TestClient(app)
    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "interpret-model:create",
            "name": "model interpret",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "sweep": {"simulation.duration_s": [10]},
        },
    )
    experiment_id = created.json()["experiment_id"]
    response = client.post(
        f"/agent/experiments/{experiment_id}/interpret",
        json={
            "question": "解释结果",
            "mode": "model",
            "provider_id": "test-evidence-provider",
        },
    )
    assert response.status_code == 422
    assert response.json()["detail"]["reason_code"] == "COMPARISON_DELTA_MISMATCH"


def test_interpret_api_model_can_only_propose_registered_parameter(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    import sat_sim.api as api_module
    import sat_sim.evidence_interpreter as interpreter_module
    from sat_sim.api import create_app
    from sat_sim.scenario_templates import instantiate_scenario_template

    evidence = _evidence()
    monkeypatch.setattr(api_module, "build_experiment_evidence", lambda **_kwargs: evidence)
    provider_path = tmp_path / "providers.json"
    provider_path.write_text(json.dumps({
        "providers": [{
            "provider_id": "test-evidence-provider",
            "label": "test",
            "backend": "openai_compatible",
            "location": "local",
            "tier": "L2",
            "model": "fake-model",
            "base_url": "http://127.0.0.1:9999/v1",
            "enabled": True,
        }]
    }), encoding="utf-8")
    monkeypatch.setenv("SAT_SIM_MODEL_PROVIDERS_FILE", str(provider_path))

    payload = _valid_payload()
    payload["next_experiments"] = [{
        "objective": "继续验证时长条件的影响。",
        "parameter_path": "simulation.duration_s",
        "values": [20, 30],
        "rationale": "保持其他已登记参数不变。",
        "estimated_runs": 2,
    }]
    backend = FakeBackend(payload)
    monkeypatch.setattr(interpreter_module, "make_text_llm_backend", lambda _config: backend)

    app = create_app(
        runs_root=tmp_path / "runs",
        artifacts_root=tmp_path / "artifacts",
        queue_database=tmp_path / "queue.sqlite",
        embedded_worker=False,
    )
    client = TestClient(app)
    created = client.post(
        "/agent/experiments",
        json={
            "operation_id": "interpret-proposal:create",
            "name": "proposal interpret",
            "base_task_spec": instantiate_scenario_template("adcs_pointing"),
            "sweep": {"simulation.duration_s": [10]},
        },
    )
    experiment_id = created.json()["experiment_id"]
    response = client.post(
        f"/agent/experiments/{experiment_id}/interpret",
        json={
            "question": "下一步怎么验证",
            "mode": "model",
            "provider_id": "test-evidence-provider",
        },
    )
    assert response.status_code == 200
    proposal = response.json()["analysis"]["interpretation"]["next_experiments"][0]
    assert proposal["parameter_path"] == "simulation.duration_s"
    assert response.json()["next_experiments_are_proposals_only"] is True


def test_next_experiment_value_must_respect_registered_range():
    payload = _valid_payload()
    payload["next_experiments"][0]["values"] = [999]
    payload["next_experiments"][0]["estimated_runs"] = 1
    interpretation = EvidenceInterpretation.model_validate(payload)
    with pytest.raises(InterpretationValidationError) as captured:
        validate_interpretation(
            interpretation,
            evidence=_evidence(),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            parameter_options=[{
                "path": "parameters.values.battery_capacity_wh",
                "type": "number",
                "minimum": 10,
                "maximum": 500,
                "enum": [],
            }],
        )
    assert captured.value.reason_code == "EXPERIMENT_VALUE_OUT_OF_SCHEMA"


def test_causal_language_is_rejected_from_observation_fields():
    payload = _valid_payload()
    payload["observations"][0]["statement"] = "容量变化导致末端状态变化。"
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=_evidence(),
            allowed_parameter_paths=["parameters.values.battery_capacity_wh"],
            backend=FakeBackend(payload),
        )
    assert captured.value.reason_code == "CAUSAL_LANGUAGE_OUTSIDE_HYPOTHESIS"


def test_model_interpretation_rejects_oversized_evidence_context():
    evidence = _evidence()
    evidence["runs"] = [
        {"run_id": f"run_{index}", "metrics": {"metric": float(index)}}
        for index in range(33)
    ]
    with pytest.raises(InterpretationValidationError) as captured:
        interpret_evidence(
            question="解释",
            evidence=evidence,
            backend=FakeBackend(_valid_payload()),
        )
    assert captured.value.reason_code == "EVIDENCE_CONTEXT_LIMIT"
