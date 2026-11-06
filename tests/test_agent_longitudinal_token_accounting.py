from __future__ import annotations

import json

from sat_sim.experience_longitudinal_eval import _model_metrics


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
