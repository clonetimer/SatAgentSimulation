#!/usr/bin/env python3
"""
用途：将历史 pip-audit 漏洞基线与当前锁文件逐项比较，验证已知漏洞所需修复版本是否已落实。
参数：--baseline 指定历史 pip-audit 原始 JSON；--requirements 指定当前锁文件；--output 指定报告。
输出：生成离线修复核验 JSON；只证明给定历史漏洞基线已修复，不替代当前联网漏洞扫描。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from packaging.version import InvalidVersion, Version


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalize_name(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_locked_versions(path: Path) -> dict[str, str]:
    versions: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        value = line.strip()
        if not value or value.startswith("#") or "==" not in value:
            continue
        name, version = value.split("==", 1)
        version = version.split(";", 1)[0].strip()
        versions[_normalize_name(name.strip())] = version
    return versions


def _baseline_dependencies(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        values = payload.get("dependencies", [])
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    return [item for item in values if isinstance(item, dict)]


def _version_meets_fix(current: str, fix_versions: list[str]) -> bool:
    if not fix_versions:
        return False
    try:
        current_version = Version(current)
        fixes = [Version(value) for value in fix_versions]
    except InvalidVersion:
        return False
    return current_version >= min(fixes)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Verify remediation against a prior pip-audit baseline")
    parser.add_argument("--baseline", required=True)
    parser.add_argument("--requirements", default="requirements-lock.txt")
    parser.add_argument("--output", default="reports/security/dependency_remediation_verification.json")
    args = parser.parse_args()

    baseline = Path(args.baseline).resolve()
    requirements = (root / args.requirements).resolve() if not Path(args.requirements).is_absolute() else Path(args.requirements)
    output = (root / args.output).resolve() if not Path(args.output).is_absolute() else Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)

    payload = json.loads(baseline.read_text(encoding="utf-8"))
    locked = _read_locked_versions(requirements)
    packages: list[dict[str, Any]] = []
    all_remediated = True
    vulnerability_count = 0
    for dependency in _baseline_dependencies(payload):
        vulns = dependency.get("vulns") or dependency.get("vulnerabilities") or []
        if not vulns:
            continue
        name = str(dependency.get("name", ""))
        normalized = _normalize_name(name)
        current = locked.get(normalized)
        checks: list[dict[str, Any]] = []
        package_ok = current is not None
        for vuln in vulns:
            vulnerability_count += 1
            fix_versions = [str(value) for value in vuln.get("fix_versions", [])]
            remediated = current is not None and _version_meets_fix(current, fix_versions)
            package_ok = package_ok and remediated
            checks.append({
                "vulnerability_id": vuln.get("id"),
                "aliases": vuln.get("aliases", []),
                "fix_versions": fix_versions,
                "remediated": remediated,
            })
        all_remediated = all_remediated and package_ok
        packages.append({
            "name": name,
            "baseline_version": str(dependency.get("version", "")),
            "current_locked_version": current,
            "vulnerability_count": len(vulns),
            "remediated": package_ok,
            "checks": checks,
        })

    report = {
        "schema_version": "sat-sim.dependency-remediation-verification.v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS_AGAINST_BASELINE_AUDIT" if all_remediated else "FAIL",
        "scope": "Offline comparison against the supplied historical pip-audit result; a fresh online audit is still required.",
        "baseline": {"filename": baseline.name, "sha256": _sha256(baseline)},
        "requirements": {"filename": requirements.name, "sha256": _sha256(requirements)},
        "baseline_vulnerability_count": vulnerability_count,
        "affected_package_count": len(packages),
        "remediated_package_count": sum(1 for item in packages if item["remediated"]),
        "packages": packages,
        "fresh_online_audit_required": True,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "affected_packages": len(packages)}, ensure_ascii=False))
    return 0 if all_remediated else 1


if __name__ == "__main__":
    raise SystemExit(main())
