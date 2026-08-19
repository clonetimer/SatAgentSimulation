#!/usr/bin/env python3
"""
用途：校验恢复工作树中的强制 WMM/SPICE 资产，并按需校验可选离线 Basilisk 制品与当前运行时身份。
参数：--require-offline-bsk 要求两个 satfix1 Wheel 和构建报告；--require-basilisk-runtime 要求当前 Python 可导入受支持的 Basilisk 2.11.0 运行时；--output 指定报告路径。
输出：生成分层 third-party 校验 JSON；默认只以强制资产完整性决定退出码，不把可选离线 Wheel 缺失判为失败。
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import importlib.metadata
import json
from pathlib import Path
from typing import Any

ACCEPTED_BSK_VERSIONS = frozenset({"2.11.0", "2.11.0+satfix1"})


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _runtime_evidence() -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "importable": False,
        "distribution_version": None,
        "version_accepted": False,
        "accepted_versions": sorted(ACCEPTED_BSK_VERSIONS),
        "module_path": None,
        "error": None,
    }
    try:
        module = importlib.import_module("Basilisk")
        locations = list(getattr(module, "__path__", ()) or ())
        evidence["module_path"] = str(locations[0]) if locations else str(getattr(module, "__file__", "") or "") or None
        evidence["distribution_version"] = importlib.metadata.version("bsk")
        evidence["version_accepted"] = evidence["distribution_version"] in ACCEPTED_BSK_VERSIONS
        evidence["importable"] = True
    except Exception as exc:
        evidence["error"] = f"{type(exc).__name__}: {exc}"
    evidence["ready"] = bool(evidence["importable"] and evidence["version_accepted"])
    return evidence


def verify(root: Path, *, require_offline_bsk: bool, require_basilisk_runtime: bool) -> dict[str, Any]:
    manifest_path = root / "third_party" / "THIRD_PARTY_MANIFEST.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    mandatory_errors: list[dict[str, Any]] = []
    optional_findings: list[dict[str, Any]] = []
    checked_files: list[dict[str, Any]] = []
    for row in manifest.get("files", []):
        relative = str(row["path"])
        path = root / relative
        required = bool(row.get("required", True))
        present = path.is_file()
        record: dict[str, Any] = {"path": relative, "required": required, "present": present}
        if not present:
            finding = {"path": relative, "error": "missing"}
            if required or (require_offline_bsk and (relative.endswith(".whl") or relative.endswith("bsk_2.11.0_satfix1_build_report.json"))):
                mandatory_errors.append(finding)
            else:
                optional_findings.append({**finding, "severity": "optional"})
            checked_files.append(record)
            continue
        actual = sha256(path)
        record["sha256"] = actual
        expected = row.get("sha256")
        if expected and actual != expected:
            finding = {"path": relative, "error": "sha256_mismatch", "actual": actual, "expected": expected}
            if required or require_offline_bsk:
                mandatory_errors.append(finding)
            else:
                optional_findings.append({**finding, "severity": "optional"})
        checked_files.append(record)

    runtime = _runtime_evidence()
    if require_basilisk_runtime and not runtime["ready"]:
        mandatory_errors.append({"path": "python:Basilisk", "error": "runtime_unavailable_or_unsupported", "details": runtime})

    offline_candidates = [item for item in checked_files if item["path"].endswith(".whl") or item["path"].endswith("bsk_2.11.0_satfix1_build_report.json")]
    offline_complete = bool(offline_candidates) and all(item["present"] for item in offline_candidates)
    status = "PASS" if not mandatory_errors else "FAIL"
    return {
        "schema_version": "sat-sim.recovery-third-party-verification.v2",
        "status": status,
        "passed": status == "PASS",
        "policy": {
            "runtime_dependency_mode": "external_python_environment",
            "offline_bsk_required": require_offline_bsk,
            "basilisk_runtime_required": require_basilisk_runtime,
        },
        "mandatory_asset_status": "PASS" if not [e for e in mandatory_errors if e.get("path") != "python:Basilisk"] else "FAIL",
        "offline_bsk_bundle_status": "PASS" if offline_complete else "OPTIONAL_NOT_PRESENT",
        "runtime_basilisk": runtime,
        "errors": mandatory_errors,
        "optional_findings": optional_findings,
        "checked_files": checked_files,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--require-offline-bsk", action="store_true")
    parser.add_argument("--require-basilisk-runtime", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    report = verify(
        root,
        require_offline_bsk=args.require_offline_bsk,
        require_basilisk_runtime=args.require_basilisk_runtime,
    )
    output = args.output or root / "reports" / "recovery_third_party_verification.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
