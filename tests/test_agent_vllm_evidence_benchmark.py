from __future__ import annotations

import json

from sat_sim.vllm_evidence_benchmark import run_vllm_evidence_benchmark


def _write_call(root, case_id, usage):
    path = root / "vllm_nl_e2e_matrix_test" / case_id / "model_call.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "invocation_evidence": {
                    "latency_ms": 10.0,
                    "verified": True,
                    "usage": usage,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def _stub_environment(monkeypatch):
    monkeypatch.setattr(
        "sat_sim.vllm_evidence_benchmark._service_status",
        lambda base_url: {"ok": True, "models": ["test-model"]},
    )
    monkeypatch.setattr(
        "sat_sim.vllm_evidence_benchmark._gpu_status",
        lambda: {"ok": True, "rows": ["0, test-gpu"]},
    )


def test_benchmark_derives_total_from_complete_token_components(tmp_path, monkeypatch):
    _stub_environment(monkeypatch)
    reports = tmp_path / "reports"
    _write_call(reports, "case-1", {"prompt_tokens": 100, "completion_tokens": 23})

    report = run_vllm_evidence_benchmark(
        reports_root=reports,
        output_path=tmp_path / "benchmark.json",
    )
    assert report["checks"]["token_usage_complete"] is True
    assert report["checks"]["p95_token_budget"] is True
    assert report["tokens"]["total_p50"] == 123
    assert report["tokens"]["total_p95"] == 123
    assert report["tokens"]["total_source_counts"]["derived_prompt_plus_completion"] == 1
    assert report["tokens"]["total_unknown_count"] == 0


def test_benchmark_does_not_count_missing_token_total_as_zero(tmp_path, monkeypatch):
    _stub_environment(monkeypatch)
    reports = tmp_path / "reports"
    _write_call(reports, "case-1", {"prompt_tokens": 100})

    report = run_vllm_evidence_benchmark(
        reports_root=reports,
        output_path=tmp_path / "benchmark.json",
    )
    assert report["checks"]["token_usage_complete"] is False
    assert report["checks"]["p95_token_budget"] is False
    assert report["tokens"]["total_p50"] is None
    assert report["tokens"]["total_p95"] is None
    assert report["tokens"]["total_max"] is None
    assert report["tokens"]["total_unknown_count"] == 1
    assert report["tokens"]["total_coverage_rate"] == 0.0
