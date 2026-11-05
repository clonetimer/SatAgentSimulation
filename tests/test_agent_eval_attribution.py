from __future__ import annotations

import json

from sat_sim.agent_eval_attribution import (
    aggregate_cases,
    analyze_case,
    analyze_generation_artifacts,
)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _model_call(path, *, tokens=10, latency=25.0, verified=True):
    _write(path, {
        "invocation_evidence": {
            "verified": verified,
            "latency_ms": latency,
            "usage": {
                "prompt_tokens": tokens - 2,
                "completion_tokens": 2,
                "total_tokens": tokens,
            },
        }
    })


def test_attribution_separates_first_answer_from_repaired_success(tmp_path):
    _write(tmp_path / "first_attempt.json", {
        "eligible_unrepaired_first_answer": True,
        "validation_ok": False,
        "validation": {"ok": False, "issues": [{"code": "BAD_FIELD"}]},
    })
    _write(tmp_path / "unified_agent_report.json", {
        "ok": True,
        "facade_result": {
            "steps": [
                {"name": "model_invocation"},
                {"name": "llm_validator_retry"},
            ]
        },
    })
    _model_call(tmp_path / "model_call.json", tokens=10)
    _model_call(tmp_path / "model_call_retry.json", tokens=12)

    row = analyze_generation_artifacts(tmp_path)
    assert row["first_answer_pass"] is False
    assert row["final_agent_ok"] is True
    assert row["repair_assisted_success"] is True
    assert row["model_retry_used"] is True
    assert row["model_calls"]["present_count"] == 2
    assert row["model_calls"]["total_tokens"] == 22


def test_first_answer_metric_excludes_template_and_external_draft_cases(tmp_path):
    _write(tmp_path / "first_attempt.json", {
        "eligible_unrepaired_first_answer": False,
        "validation_ok": True,
    })
    _write(tmp_path / "unified_agent_report.json", {"ok": True})
    row = analyze_generation_artifacts(tmp_path)
    assert row["first_answer_eligible"] is False
    assert row["first_answer_pass"] is False
    assert row["final_agent_ok"] is True


def test_case_and_aggregate_keep_independent_denominators(tmp_path):
    out1 = tmp_path / "case1"
    out2 = tmp_path / "case2"
    for out, first_ok, final_ok in ((out1, True, True), (out2, False, True)):
        _write(out / "first_attempt.json", {
            "eligible_unrepaired_first_answer": True,
            "validation_ok": first_ok,
        })
        _write(out / "unified_agent_report.json", {"ok": final_ok})
        _model_call(out / "model_call.json", tokens=10)

    run1 = tmp_path / "run1"
    _write(run1 / "run_record.json", {"status": "SUCCEEDED"})
    _write(run1 / "validation" / "validation_outcome.json", {"result": "PASS"})
    _write(run1 / "SEALED.json", {"ok": True})

    case1 = analyze_case(
        case_id="c1",
        output_dir=out1,
        bundle_root=run1,
        evidence_checks={"number_matches": True, "citation_matches": True},
    )
    case2 = analyze_case(
        case_id="c2",
        output_dir=out2,
        bundle_root=None,
        evidence_checks={},
    )
    report = aggregate_cases([case1, case2])

    assert report["metrics"]["first_answer"] == {
        "eligible": 2,
        "passed": 1,
        "rate": 0.5,
    }
    assert report["metrics"]["final_agent_task"]["rate"] == 1.0
    assert report["metrics"]["native_execution"] == {
        "eligible": 1,
        "passed": 1,
        "rate": 1.0,
    }
    assert report["metrics"]["evidence_accuracy"] == {
        "eligible": 1,
        "passed": 1,
        "rate": 1.0,
    }
    assert report["metrics"]["repair_assisted_success_count"] == 1
    assert report["metrics"]["resource_use"]["model_total_tokens"] == 20
