from __future__ import annotations

import hashlib
import json
from pathlib import Path

from sat_sim.external_gate import (
    WINDOWS_REQUIRED_CHECKS,
    build_unified_external_gate_report,
    evaluate_lmstudio_report,
    evaluate_windows_summary,
    is_loopback_url,
    verify_windows_evidence_manifest,
)
from sat_sim.release_closure import RELEASE_ID, RELEASE_VERSION


def _windows_pass() -> dict:
    return {
        "schema_version": "sat-sim.windows-target-acceptance.v2",
        "release_id": RELEASE_ID,
        "release_version": RELEASE_VERSION,
        "target_os": "Windows",
        "actual_target_execution": True,
        "status": "PASS_WITH_ADVISORY",
        "core_platform_status": "PASS",
        "host_fingerprint_sha256": "a" * 64,
        "evidence_manifest_sha256": "b" * 64,
        "checks": [
            {"name": name, "passed": True, "required": True, "detail": "ok"}
            for name in WINDOWS_REQUIRED_CHECKS
        ],
    }


def _lmstudio_pass(base_url: str = "http://127.0.0.1:1234/v1") -> dict:
    evidence = {
        "verified": True,
        "model_id": "qwen/test",
        "request_id": "request-1",
        "prompt_sha256": "a" * 64,
        "response_sha256": "b" * 64,
        "structured_output_sha256": "c" * 64,
    }
    return {
        "schema_version": "sat-sim.lmstudio-acceptance.v2",
        "release_version": RELEASE_VERSION,
        "execution_kind": "actual_lmstudio",
        "overall_status": "PASS",
        "ok": True,
        "base_url": base_url,
        "model": "qwen/test",
        "openai_models": {"ok": True},
        "native_models": {"ok": True},
        "structured_probe": {"ok": True},
        "taskspec_cases": [
            {
                "case_id": case_id,
                "passed": True,
                "checks": {
                    "actual_model_invocation_verified": True,
                    "no_fallback": True,
                },
                "invocation_evidence": evidence,
            }
            for case_id in ("adcs_create", "adcs_refine", "whole_create")
        ],
    }


def test_release_identity_is_v0570() -> None:
    manifest = json.loads((Path(__file__).resolve().parents[1] / "src/sat_sim/release_manifest.json").read_text())
    assert RELEASE_VERSION == manifest["release_version"]
    assert RELEASE_ID == manifest["release_id"]


def test_loopback_detection_is_strict() -> None:
    assert is_loopback_url("http://127.0.0.1:1234/v1") is True
    assert is_loopback_url("http://localhost:1234/v1") is True
    assert is_loopback_url("http://[::1]:1234/v1") is True
    assert is_loopback_url("http://192.168.1.10:1234/v1") is False


def test_missing_evidence_remains_pending() -> None:
    report = build_unified_external_gate_report(expected_version=RELEASE_VERSION)
    assert report.status == "PENDING_EXTERNAL_GATES"
    assert report.ok is False
    assert report.windows_gate["status"] == "PENDING"
    assert report.lmstudio_gate["status"] == "PENDING"


def test_exact_windows_evidence_passes_and_version_mismatch_fails() -> None:
    payload = _windows_pass()
    result = evaluate_windows_summary(payload)
    assert result["passed"] is True
    payload["release_version"] = "0.5.6.6"
    result = evaluate_windows_summary(payload)
    assert result["passed"] is False
    assert "WINDOWS_RELEASE_VERSION_MISMATCH" in result["reason_codes"]


def test_lmstudio_requires_native_identity_invocation_evidence_and_no_fallback() -> None:
    payload = _lmstudio_pass()
    assert evaluate_lmstudio_report(payload, require_loopback=True)["passed"] is True
    payload["native_models"] = {"ok": False}
    result = evaluate_lmstudio_report(payload, require_loopback=True)
    assert result["passed"] is False
    assert "LMSTUDIO_NATIVE_IDENTITY_NOT_PROVEN" in result["reason_codes"]

    payload = _lmstudio_pass()
    payload["taskspec_cases"][0]["checks"]["no_fallback"] = False
    result = evaluate_lmstudio_report(payload)
    assert "LMSTUDIO_FALLBACK_DETECTED" in result["reason_codes"]


def test_unified_gate_only_passes_when_both_exact_gates_pass(tmp_path: Path) -> None:
    windows = tmp_path / "windows.json"
    model = tmp_path / "model.json"
    evidence = tmp_path / "doctor.log"
    evidence.write_text("doctor pass", encoding="utf-8")
    evidence_bytes = evidence.read_bytes()
    manifest = {
        "schema_version": "sat-sim.windows-evidence-manifest.v1",
        "release_version": RELEASE_VERSION,
        "file_count": 1,
        "files": [{
            "path": "doctor.log",
            "size_bytes": len(evidence_bytes),
            "sha256": hashlib.sha256(evidence_bytes).hexdigest(),
        }],
    }
    manifest_path = tmp_path / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    windows_payload = _windows_pass()
    windows_payload["evidence_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    windows.write_text(json.dumps(windows_payload), encoding="utf-8")
    model.write_text(json.dumps(_lmstudio_pass()), encoding="utf-8")
    report = build_unified_external_gate_report(
        windows_summary_path=windows,
        lmstudio_report_path=model,
        expected_version=RELEASE_VERSION,
        require_loopback=True,
    )
    assert report.status == "PASS"
    assert report.ok is True
    assert report.evidence_files["windows_summary"]["sha256"]
    assert report.evidence_files["lmstudio_report"]["sha256"]


def test_windows_evidence_manifest_accepts_zero_byte_file(tmp_path: Path) -> None:
    empty = tmp_path / "package_identity.stderr.log"
    empty.write_bytes(b"")
    manifest = {
        "schema_version": "sat-sim.windows-evidence-manifest.v1",
        "release_version": RELEASE_VERSION,
        "file_count": 1,
        "files": [{
            "path": empty.name,
            "size_bytes": 0,
            "sha256": hashlib.sha256(b"").hexdigest(),
        }],
    }
    manifest_path = tmp_path / "evidence_manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    summary = _windows_pass()
    summary["evidence_manifest_sha256"] = hashlib.sha256(manifest_path.read_bytes()).hexdigest()

    result = verify_windows_evidence_manifest(tmp_path / "windows.json", summary)

    assert result["passed"] is True
    assert result["reason_codes"] == []
    assert result["details"]["mismatched_files"] == []


def test_platform_script_contains_strict_antioverclaim_checks() -> None:
    text = Path("scripts/run_platform_acceptance.py").read_text(encoding="utf-8")
    for token in (
        "sat-sim.platform-acceptance.v1",
        "package_release_identity",
        "strict_doctor_12_of_12",
        "strict_release_check",
        "run_bundle_sealed",
        "run_bundle_integrity",
        "evidence_manifest.json",
        "--require-loopback",
    ):
        assert token in text
