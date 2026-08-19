"""Strict evaluation for the current Windows and real-LM-Studio gates.

This module is deliberately conservative. Missing evidence remains pending;
malformed or contradictory evidence fails; only evidence produced by the exact
release and satisfying every required check can close a gate.
"""
from __future__ import annotations

import hashlib
import ipaddress
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse

from .release_closure import RELEASE_ID, RELEASE_VERSION

UNIFIED_GATE_SCHEMA_VERSION = "sat-sim.unified-external-gate.v1"
WINDOWS_GATE_SCHEMA_VERSION = "sat-sim.windows-target-acceptance.v2"
LMSTUDIO_GATE_SCHEMA_VERSION = "sat-sim.lmstudio-acceptance.v2"

WINDOWS_REQUIRED_CHECKS = (
    "windows_os",
    "python_executable",
    "python_version",
    "package_release_identity",
    "environment_doctor",
    "environment_doctor_12_of_12",
    "unified_native_run",
    "run_bundle_sealed",
    "run_bundle_integrity",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _read_json(path: str | Path | None) -> tuple[dict[str, Any] | None, str | None]:
    if path is None:
        return None, None
    candidate = Path(path)
    if not candidate.is_file():
        return None, f"Evidence file does not exist: {candidate}"
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return None, f"Invalid JSON evidence {candidate}: {exc}"
    if not isinstance(payload, dict):
        return None, f"Evidence root must be an object: {candidate}"
    return payload, None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_sha256(value: Any) -> bool:
    text = str(value or "")
    return len(text) == 64 and all(char in "0123456789abcdefABCDEF" for char in text)


def verify_windows_evidence_manifest(
    summary_path: str | Path,
    summary: Mapping[str, Any],
) -> dict[str, Any]:
    root = Path(summary_path).resolve().parent
    manifest_path = root / "evidence_manifest.json"
    reasons: list[str] = []
    if not manifest_path.is_file():
        return {"passed": False, "reason_codes": ["WINDOWS_EVIDENCE_MANIFEST_MISSING"], "details": {"path": str(manifest_path)}}
    actual_manifest_hash = sha256_file(manifest_path)
    expected_manifest_hash = str(summary.get("evidence_manifest_sha256") or "")
    if not _is_sha256(expected_manifest_hash) or actual_manifest_hash != expected_manifest_hash.lower():
        reasons.append("WINDOWS_EVIDENCE_MANIFEST_HASH_MISMATCH")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    except Exception as exc:
        return {"passed": False, "reason_codes": ["WINDOWS_EVIDENCE_MANIFEST_INVALID"], "details": {"error": str(exc)}}
    if not isinstance(manifest, Mapping):
        return {"passed": False, "reason_codes": ["WINDOWS_EVIDENCE_MANIFEST_INVALID"], "details": {}}
    if str(manifest.get("release_version")) != str(summary.get("release_version")):
        reasons.append("WINDOWS_EVIDENCE_MANIFEST_RELEASE_MISMATCH")
    rows = manifest.get("files") if isinstance(manifest.get("files"), list) else []
    if int(manifest.get("file_count") or -1) != len(rows):
        reasons.append("WINDOWS_EVIDENCE_MANIFEST_COUNT_MISMATCH")
    invalid_rows: list[str] = []
    missing_files: list[str] = []
    mismatched_files: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping):
            invalid_rows.append("<non-object>")
            continue
        relative = str(row.get("path") or "")
        rel_path = Path(relative)
        if not relative or rel_path.is_absolute() or ".." in rel_path.parts:
            invalid_rows.append(relative or "<empty>")
            continue
        target = (root / rel_path).resolve()
        try:
            target.relative_to(root)
        except ValueError:
            invalid_rows.append(relative)
            continue
        if not target.is_file():
            missing_files.append(relative)
            continue
        expected_size = row.get("size_bytes")
        expected_hash = str(row.get("sha256") or "")
        size_matches = expected_size is not None and int(expected_size) == target.stat().st_size
        if not size_matches or not _is_sha256(expected_hash) or sha256_file(target) != expected_hash.lower():
            mismatched_files.append(relative)
    if invalid_rows:
        reasons.append("WINDOWS_EVIDENCE_MANIFEST_UNSAFE_PATH")
    if missing_files:
        reasons.append("WINDOWS_EVIDENCE_FILES_MISSING")
    if mismatched_files:
        reasons.append("WINDOWS_EVIDENCE_FILES_HASH_MISMATCH")
    return {
        "passed": not reasons,
        "reason_codes": reasons,
        "details": {
            "manifest_path": str(manifest_path),
            "manifest_sha256": actual_manifest_hash,
            "file_count": len(rows),
            "invalid_rows": invalid_rows,
            "missing_files": missing_files,
            "mismatched_files": mismatched_files,
        },
    }


def is_loopback_url(value: str) -> bool:
    try:
        parsed = urlparse(str(value))
        host = parsed.hostname
        if not host:
            return False
        if host.lower() == "localhost":
            return True
        return ipaddress.ip_address(host).is_loopback
    except Exception:
        return False


def _check_map(payload: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    rows = payload.get("checks")
    if not isinstance(rows, list):
        return {}
    return {
        str(item.get("name")): item
        for item in rows
        if isinstance(item, Mapping) and item.get("name")
    }


def evaluate_windows_summary(
    payload: Mapping[str, Any] | None,
    *,
    expected_version: str = RELEASE_VERSION,
    expected_release_id: str = RELEASE_ID,
) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "PENDING",
            "passed": False,
            "reason_codes": ["WINDOWS_EVIDENCE_MISSING"],
            "details": {},
        }
    reasons: list[str] = []
    checks = _check_map(payload)
    schema_ok = payload.get("schema_version") == WINDOWS_GATE_SCHEMA_VERSION
    if not schema_ok:
        reasons.append("WINDOWS_SCHEMA_VERSION_MISMATCH")
    if str(payload.get("target_os")) != "Windows" or payload.get("actual_target_execution") is not True:
        reasons.append("WINDOWS_ACTUAL_TARGET_NOT_PROVEN")
    if str(payload.get("release_version")) != expected_version:
        reasons.append("WINDOWS_RELEASE_VERSION_MISMATCH")
    if str(payload.get("release_id")) != expected_release_id:
        reasons.append("WINDOWS_RELEASE_ID_MISMATCH")
    missing = [name for name in WINDOWS_REQUIRED_CHECKS if name not in checks]
    failed = [name for name in WINDOWS_REQUIRED_CHECKS if name in checks and checks[name].get("passed") is not True]
    if missing:
        reasons.append("WINDOWS_REQUIRED_CHECKS_MISSING")
    if failed:
        reasons.append("WINDOWS_REQUIRED_CHECKS_FAILED")
    if payload.get("core_platform_status") != "PASS":
        reasons.append("WINDOWS_CORE_PLATFORM_NOT_PASS")
    # PASS_WITH_ADVISORY is allowed only for explicitly optional checks.
    if payload.get("status") not in {"PASS", "PASS_WITH_ADVISORY"}:
        reasons.append("WINDOWS_SUMMARY_NOT_PASS")
    if not payload.get("host_fingerprint_sha256"):
        reasons.append("WINDOWS_HOST_FINGERPRINT_MISSING")
    if not payload.get("evidence_manifest_sha256"):
        reasons.append("WINDOWS_EVIDENCE_MANIFEST_HASH_MISSING")
    passed = not reasons
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "reason_codes": reasons,
        "details": {
            "schema_ok": schema_ok,
            "missing_required_checks": missing,
            "failed_required_checks": failed,
            "summary_status": payload.get("status"),
            "core_platform_status": payload.get("core_platform_status"),
            "release_version": payload.get("release_version"),
            "host_fingerprint_sha256": payload.get("host_fingerprint_sha256"),
            "evidence_manifest_sha256": payload.get("evidence_manifest_sha256"),
        },
    }


def evaluate_lmstudio_report(
    payload: Mapping[str, Any] | None,
    *,
    expected_version: str = RELEASE_VERSION,
    require_loopback: bool = False,
) -> dict[str, Any]:
    if payload is None:
        return {
            "status": "PENDING",
            "passed": False,
            "reason_codes": ["LMSTUDIO_EVIDENCE_MISSING"],
            "details": {},
        }
    reasons: list[str] = []
    if payload.get("schema_version") != LMSTUDIO_GATE_SCHEMA_VERSION:
        reasons.append("LMSTUDIO_SCHEMA_VERSION_MISMATCH")
    if str(payload.get("release_version")) != expected_version:
        reasons.append("LMSTUDIO_RELEASE_VERSION_MISMATCH")
    if payload.get("execution_kind") != "actual_lmstudio":
        reasons.append("LMSTUDIO_NOT_ACTUAL_EXECUTION")
    if payload.get("overall_status") != "PASS" or payload.get("ok") is not True:
        reasons.append("LMSTUDIO_OVERALL_NOT_PASS")
    if not str(payload.get("model") or "").strip():
        reasons.append("LMSTUDIO_MODEL_ID_MISSING")
    if not isinstance(payload.get("openai_models"), Mapping) or payload["openai_models"].get("ok") is not True:
        reasons.append("LMSTUDIO_OPENAI_API_NOT_PROVEN")
    if not isinstance(payload.get("native_models"), Mapping) or payload["native_models"].get("ok") is not True:
        reasons.append("LMSTUDIO_NATIVE_IDENTITY_NOT_PROVEN")
    if not isinstance(payload.get("structured_probe"), Mapping) or payload["structured_probe"].get("ok") is not True:
        reasons.append("LMSTUDIO_STRUCTURED_OUTPUT_NOT_PROVEN")
    cases = payload.get("taskspec_cases") if isinstance(payload.get("taskspec_cases"), list) else []
    if not cases:
        reasons.append("LMSTUDIO_TASKSPEC_CASES_MISSING")
    failed_cases = [str(item.get("case_id")) for item in cases if not isinstance(item, Mapping) or item.get("passed") is not True]
    if failed_cases:
        reasons.append("LMSTUDIO_TASKSPEC_CASES_FAILED")
    unverified = []
    fallback = []
    for item in cases:
        if not isinstance(item, Mapping):
            continue
        case_id = str(item.get("case_id"))
        checks = item.get("checks") if isinstance(item.get("checks"), Mapping) else {}
        evidence = item.get("invocation_evidence") if isinstance(item.get("invocation_evidence"), Mapping) else {}
        evidence_complete = bool(
            evidence.get("verified") is True
            and evidence.get("model_id")
            and evidence.get("request_id")
            and _is_sha256(evidence.get("prompt_sha256"))
            and _is_sha256(evidence.get("response_sha256"))
            and _is_sha256(evidence.get("structured_output_sha256"))
        )
        if checks.get("actual_model_invocation_verified") is not True or not evidence_complete:
            unverified.append(case_id)
        if checks.get("no_fallback") is not True:
            fallback.append(case_id)
    if unverified:
        reasons.append("LMSTUDIO_INVOCATION_EVIDENCE_INCOMPLETE")
    if fallback:
        reasons.append("LMSTUDIO_FALLBACK_DETECTED")
    if require_loopback and not is_loopback_url(str(payload.get("base_url") or "")):
        reasons.append("LMSTUDIO_LOOPBACK_REQUIRED")
    passed = not reasons
    return {
        "status": "PASS" if passed else "FAIL",
        "passed": passed,
        "reason_codes": reasons,
        "details": {
            "overall_status": payload.get("overall_status"),
            "model": payload.get("model"),
            "base_url": payload.get("base_url"),
            "loopback": is_loopback_url(str(payload.get("base_url") or "")),
            "case_count": len(cases),
            "failed_cases": failed_cases,
            "unverified_invocation_cases": unverified,
            "fallback_cases": fallback,
        },
    }


@dataclass
class UnifiedExternalGateReport:
    schema_version: str = UNIFIED_GATE_SCHEMA_VERSION
    release_id: str = RELEASE_ID
    release_version: str = RELEASE_VERSION
    generated_at_utc: str = field(default_factory=_utc_now)
    status: str = "PENDING_EXTERNAL_GATES"
    ok: bool = False
    windows_gate: dict[str, Any] = field(default_factory=dict)
    lmstudio_gate: dict[str, Any] = field(default_factory=dict)
    evidence_files: dict[str, Any] = field(default_factory=dict)
    claim_boundary: str = (
        "PASS may be issued only when exact-release Windows evidence and real LM Studio evidence both pass strict evaluation."
    )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def build_unified_external_gate_report(
    *,
    windows_summary_path: str | Path | None = None,
    lmstudio_report_path: str | Path | None = None,
    expected_version: str = RELEASE_VERSION,
    expected_release_id: str = RELEASE_ID,
    require_loopback: bool = False,
) -> UnifiedExternalGateReport:
    windows_payload, windows_error = _read_json(windows_summary_path)
    lm_payload, lm_error = _read_json(lmstudio_report_path)
    windows = evaluate_windows_summary(
        windows_payload,
        expected_version=expected_version,
        expected_release_id=expected_release_id,
    )
    if windows_payload is not None and windows_summary_path is not None and windows.get("status") != "PENDING":
        manifest_verification = verify_windows_evidence_manifest(windows_summary_path, windows_payload)
        windows.setdefault("details", {})["evidence_manifest_verification"] = manifest_verification
        if not manifest_verification["passed"]:
            windows["passed"] = False
            windows["status"] = "FAIL"
            windows["reason_codes"] = list(dict.fromkeys([
                *windows.get("reason_codes", []),
                *manifest_verification.get("reason_codes", []),
            ]))
    lmstudio = evaluate_lmstudio_report(
        lm_payload,
        expected_version=expected_version,
        require_loopback=require_loopback,
    )
    if windows_error and windows_summary_path is not None:
        windows = {"status": "FAIL", "passed": False, "reason_codes": ["WINDOWS_EVIDENCE_INVALID"], "details": {"error": windows_error}}
    if lm_error and lmstudio_report_path is not None:
        lmstudio = {"status": "FAIL", "passed": False, "reason_codes": ["LMSTUDIO_EVIDENCE_INVALID"], "details": {"error": lm_error}}

    if windows["passed"] and lmstudio["passed"]:
        status = "PASS"
    elif windows["status"] == "FAIL" or lmstudio["status"] == "FAIL":
        status = "FAIL"
    elif windows["passed"]:
        status = "PASS_WITH_EXTERNAL_MODEL_GATE"
    elif lmstudio["passed"]:
        status = "PASS_WITH_EXTERNAL_WINDOWS_GATE"
    else:
        status = "PENDING_EXTERNAL_GATES"

    files: dict[str, Any] = {}
    for key, path in (("windows_summary", windows_summary_path), ("lmstudio_report", lmstudio_report_path)):
        if path is None:
            files[key] = None
        else:
            candidate = Path(path)
            files[key] = {
                "path": str(candidate),
                "exists": candidate.is_file(),
                "sha256": sha256_file(candidate) if candidate.is_file() else None,
            }
    return UnifiedExternalGateReport(
        release_id=expected_release_id,
        release_version=expected_version,
        status=status,
        ok=status == "PASS",
        windows_gate=windows,
        lmstudio_gate=lmstudio,
        evidence_files=files,
    )


__all__ = [
    "LMSTUDIO_GATE_SCHEMA_VERSION",
    "UNIFIED_GATE_SCHEMA_VERSION",
    "WINDOWS_GATE_SCHEMA_VERSION",
    "WINDOWS_REQUIRED_CHECKS",
    "UnifiedExternalGateReport",
    "build_unified_external_gate_report",
    "evaluate_lmstudio_report",
    "evaluate_windows_summary",
    "is_loopback_url",
    "sha256_file",
    "verify_windows_evidence_manifest",
]
