from __future__ import annotations

import hashlib
import json

from sat_sim.agent_eval_attribution import (
    aggregate_cases,
    analyze_case,
    analyze_generation_artifacts,
)


def _write(path, payload):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _model_call(path, *, tokens=10, latency=25.0, verified=True, request_id=None):
    _write(path, {
        "invocation_evidence": {
            "verified": verified,
            "latency_ms": latency,
            "request_id": request_id,
            "usage": {
                "prompt_tokens": tokens - 2,
                "completion_tokens": 2,
                "total_tokens": tokens,
            },
        }
    })




def _sealed_bundle(root, *, implementation_class="official_basilisk_native", run_id="eval-run"):
    payloads = {
        "run_record.json": {"run_id": run_id, "status": "SUCCEEDED"},
        "validation/validation_outcome.json": {"result": "PASS"},
        "input/resolved_spec.json": {"implementation_class": implementation_class},
    }
    files = {}
    for relative, payload in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        path.write_bytes(raw)
        files[relative] = {
            "sha256": hashlib.sha256(raw).hexdigest(),
            "size_bytes": len(raw),
        }
    manifest = {"run_id": run_id, "files": files}
    manifest_raw = json.dumps(
        manifest,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    (root / "bundle_manifest.json").write_bytes(manifest_raw)
    seal = {
        "run_id": run_id,
        "bundle_manifest_sha256": hashlib.sha256(manifest_raw).hexdigest(),
    }
    _write(root / "SEALED.json", seal)
    return root

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
    assert row["repair_assisted_success"] is False


def test_case_and_aggregate_keep_independent_denominators(tmp_path):
    out1 = tmp_path / "case1"
    out2 = tmp_path / "case2"
    for out, first_ok, final_ok in ((out1, True, True), (out2, False, True)):
        _write(out / "first_attempt.json", {
            "first_answer_eligible": True,
            "first_answer_pass": first_ok,
            "eligible_unrepaired_first_answer": True,
            "validation_ok": first_ok,
        })
        report = {"ok": final_ok}
        if out == out2:
            report["facade_result"] = {"steps": [{"name": "repair"}]}
        _write(out / "unified_agent_report.json", report)
        _model_call(out / "model_call.json", tokens=10)

    _write(out1 / "experience_advisory.json", {
        "lesson_ids": ["lesson_123"],
        "retrieval_event_id": "retrieval-1",
        "influence_status": "context_injected_use_unverified",
        "retrieval_trace": {
            "event_id": "retrieval-1",
            "trace_sha256": "a" * 64,
            "retrieval_status": "matched",
            "returned_count": 1,
            "returned_lesson_ids": ["lesson_123"],
        },
    })

    run1 = _sealed_bundle(tmp_path / "run1")

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
    assert report["metrics"]["run_bundle_execution"] == {
        "eligible": 1,
        "passed": 1,
        "rate": 1.0,
    }
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
    assert report["metrics"]["experience_reuse_exposure"] == {
        "eligible": 1,
        "retrieval_with_lessons": 1,
        "context_injected_unverified": 1,
        "retrieval_rate": 1.0,
    }
    assert case1["generation"]["experience_context"]["retrieval_event_id"] == "retrieval-1"
    assert report["metrics"]["resource_use"]["model_total_tokens"] == 20


def test_schema_rejected_first_response_is_correction_and_costs_both_calls(tmp_path):
    _write(tmp_path / "first_attempt.json", {
        "first_answer_eligible": True,
        "first_answer_pass": False,
        "eligible_unrepaired_first_answer": False,
        "validation_ok": True,
        "schema_rejected_first_response": True,
        "pre_validation_corrections": ["model_invocation_rejected"],
    })
    _write(tmp_path / "unified_agent_report.json", {
        "ok": True,
        "facade_result": {
            "steps": [
                {"name": "model_invocation_rejected"},
                {"name": "model_invocation"},
            ]
        },
    })
    _model_call(
        tmp_path / "model_call_rejected.json",
        tokens=13,
        latency=30.0,
        request_id="request-rejected",
    )
    _model_call(
        tmp_path / "model_call.json",
        tokens=17,
        latency=40.0,
        request_id="request-corrected",
    )

    row = analyze_generation_artifacts(tmp_path)
    assert row["schema_rejected_first_response"] is True
    assert row["first_answer_eligible"] is True
    assert row["first_answer_pass"] is False
    assert row["correction_used"] is True
    assert row["repair_assisted_success"] is True
    assert row["model_calls"]["present_count"] == 2
    assert row["model_calls"]["total_tokens"] == 30
    assert row["model_calls"]["latency_ms"] == 70.0


def test_model_call_cost_deduplicates_same_request_id(tmp_path):
    _write(tmp_path / "first_attempt.json", {
        "first_answer_eligible": True,
        "first_answer_pass": False,
        "eligible_unrepaired_first_answer": False,
        "validation_ok": True,
        "schema_rejected_first_response": True,
    })
    _write(tmp_path / "unified_agent_report.json", {"ok": True})
    _model_call(
        tmp_path / "model_call_rejected.json",
        tokens=19,
        latency=50.0,
        request_id="same-request",
    )
    _model_call(
        tmp_path / "model_call.json",
        tokens=19,
        latency=50.0,
        request_id="same-request",
    )

    row = analyze_generation_artifacts(tmp_path)
    assert row["model_calls"]["present_count"] == 1
    assert row["model_calls"]["total_tokens"] == 19
    assert row["model_calls"]["latency_ms"] == 50.0


def test_proxy_bundle_is_not_counted_as_native_execution(tmp_path):
    bundle = _sealed_bundle(
        tmp_path / "proxy",
        implementation_class="engineering_proxy",
        run_id="proxy-run",
    )
    execution = analyze_case(
        case_id="proxy-case",
        output_dir=tmp_path / "no-generation-artifacts",
        bundle_root=bundle,
    )["execution"]
    assert execution["integrity_ok"] is True
    assert execution["run_bundle_execution_pass"] is True
    assert execution["native_eligible"] is False
    assert execution["native_execution_pass"] is False

    aggregate = aggregate_cases([{
        "generation": {},
        "execution": execution,
        "evidence_accuracy_pass": None,
    }])
    assert aggregate["metrics"]["run_bundle_execution"]["rate"] == 1.0
    assert aggregate["metrics"]["native_execution"]["eligible"] == 0
    assert aggregate["metrics"]["native_execution"]["rate"] is None


def test_tampered_run_bundle_is_not_counted_as_execution_pass(tmp_path):
    bundle = _sealed_bundle(tmp_path / "tampered")
    (bundle / "run_record.json").write_text(
        json.dumps({"run_id": "eval-run", "status": "SUCCEEDED", "tampered": True}),
        encoding="utf-8",
    )
    execution = analyze_case(
        case_id="tampered-case",
        output_dir=tmp_path / "no-generation-artifacts",
        bundle_root=bundle,
    )["execution"]
    assert execution["present"] is True
    assert execution["integrity_ok"] is False
    assert execution["run_bundle_execution_pass"] is False
    assert execution["native_eligible"] is True
    assert execution["native_execution_pass"] is False
