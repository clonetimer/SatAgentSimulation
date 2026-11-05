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
