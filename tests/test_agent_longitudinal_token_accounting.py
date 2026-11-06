from __future__ import annotations

import json

from sat_sim.experience_longitudinal_eval import _model_metrics, _paired_cost_statistics


def test_longitudinal_model_metrics_derives_total_without_provider_total(tmp_path):
    (tmp_path / "model_call.json").write_text(
        json.dumps(
            {
                "invocation_evidence": {
                    "verified": True,
                    "latency_ms": 12.5,
                    "usage": {
                        "prompt_tokens": 100,
                        "completion_tokens": 25,
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    metrics = _model_metrics(tmp_path)
    assert metrics["prompt_tokens"] == 100
    assert metrics["completion_tokens"] == 25
    assert metrics["reported_total_tokens"] is None
    assert metrics["total_tokens"] == 125
    assert metrics["total_tokens_source"] == "derived_prompt_plus_completion"


def test_longitudinal_model_metrics_preserves_unknown_total(tmp_path):
    (tmp_path / "model_call.json").write_text(
        json.dumps(
            {
                "invocation_evidence": {
                    "verified": True,
                    "latency_ms": 12.5,
                    "usage": {"prompt_tokens": 100},
                }
            }
        ),
        encoding="utf-8",
    )
    metrics = _model_metrics(tmp_path)
    assert metrics["total_tokens"] is None
    assert metrics["total_tokens_known"] is False
    assert metrics["total_tokens_source"] == "missing"


def test_pre_model_rejection_with_no_call_artifact_returns_unknown_not_exception(tmp_path):
    # Upstream preflight may reject syntax before any LLM call is made.
    output = _model_metrics(tmp_path)
    assert output["model_call_present"] is False
    assert output["model_call_evidence_error"] == "MODEL_CALL_EVIDENCE_MISSING"
    assert output["verified"] is False
    assert output["prompt_tokens"] is None
    assert output["completion_tokens"] is None
    assert output["total_tokens"] is None
    assert output["latency_ms"] is None


def test_invalid_model_call_artifact_is_recorded_not_crashed(tmp_path):
    (tmp_path / "model_call.json").write_text("{malformed-json", encoding="utf-8")
    output = _model_metrics(tmp_path)
    assert output["model_call_present"] is True
    assert output["model_call_evidence_error"] == "MODEL_CALL_EVIDENCE_INVALID"
    assert output["total_tokens"] is None


def test_all_unknown_paired_token_costs_do_not_call_median_on_empty_list():
    cases = [
        {"delta": {"attributed_total_tokens_saved": None, "attributed_latency_ms_saved": None}}
        for _ in range(6)
    ]
    summary = _paired_cost_statistics(cases)
    assert summary["token_pair_count"] == 0
    assert summary["token_unknown_pair_count"] == 6
    assert summary["token_usage_complete"] is False
    assert summary["token_wins"] == 0
    assert summary["token_losses"] == 0
    assert summary["token_ties"] == 0
    assert summary["median_total_tokens_saved"] is None
    assert summary["median_latency_ms_saved"] is None
    assert summary["median_total_tokens_saved_positive"] is False
    assert summary["token_sign_test_p_value"] == 1.0


def test_incomplete_cost_pairs_are_excluded_not_zero_imputed():
    cases = [
        {"delta": {"attributed_total_tokens_saved": -20, "attributed_latency_ms_saved": 5}},
        {"delta": {"attributed_total_tokens_saved": None, "attributed_latency_ms_saved": 4}},
    ]
    summary = _paired_cost_statistics(cases)
    assert summary["token_pair_count"] == 1
    assert summary["token_unknown_pair_count"] == 1
    assert summary["token_usage_complete"] is False
    assert summary["token_losses"] == 1
    assert summary["token_ties"] == 0
    assert summary["median_total_tokens_saved"] == -20
    assert summary["median_total_tokens_saved_positive"] is False
