#!/usr/bin/env python3
"""
用途：在 fresh pip-audit 暂不可达时，对未变化的锁定依赖集合执行限时、可证明的审计继承。
参数：--requirements、--aliases、--prior-report、--prior-raw、--prior-audit-requirements、--max-age-hours、--output。
输出：机器可读安全门禁报告；仅在原审计有效、依赖逐项一致且未超过时限时返回 0。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python 3.10
    import tomli as tomllib


NAME_RE = re.compile(r"[-_.]+")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _normalized_name(name: str) -> str:
    return NAME_RE.sub("-", name).lower()


def _parse_pinned(lines: list[str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in lines:
        text = raw.strip()
        if not text or text.startswith("#"):
            continue
        if ";" in text:
            text = text.split(";", 1)[0].strip()
        if "==" not in text:
            raise ValueError(f"dependency is not exactly pinned: {raw!r}")
        name, version = text.split("==", 1)
        normalized = _normalized_name(name.strip())
        if not normalized or not version.strip():
            raise ValueError(f"invalid pinned dependency: {raw!r}")
        if normalized in result:
            raise ValueError(f"duplicate dependency: {normalized}")
        result[normalized] = version.strip()
    return result


def _load_aliases(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    payload = json.loads(path.read_text(encoding="utf-8"))
    result: dict[str, str] = {}
    for item in payload.get("aliases", []):
        if not isinstance(item, dict):
            continue
        installed = str(item.get("installed_requirement") or "").strip()
        audit_as = str(item.get("audit_requirement") or "").strip()
        if installed and audit_as:
            result[installed.lower()] = audit_as
    return result


def _current_audit_dependencies(requirements: Path, aliases: Path) -> dict[str, str]:
    alias_map = _load_aliases(aliases)
    transformed: list[str] = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        transformed.append(alias_map.get(line.strip().lower(), line))
    return _parse_pinned(transformed)


def _raw_dependencies(payload: Any) -> dict[str, str]:
    values = payload.get("dependencies", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return {}
    result: dict[str, str] = {}
    for item in values:
        if not isinstance(item, dict):
            continue
        name = _normalized_name(str(item.get("name") or ""))
        version = str(item.get("version") or "").strip()
        if name and version:
            result[name] = version
    return result


def _vulnerability_count(payload: Any) -> int:
    values = payload.get("dependencies", []) if isinstance(payload, dict) else payload
    if not isinstance(values, list):
        return 0
    return sum(
        len((item.get("vulns") or item.get("vulnerabilities") or []))
        for item in values if isinstance(item, dict)
    )


def _release_identity(root: Path) -> dict[str, str]:
    pyproject = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    version = str(pyproject["project"]["version"])
    identity_path = root / "src" / "sat_sim" / "release_manifest.json"
    identity = json.loads(identity_path.read_text(encoding="utf-8")) if identity_path.is_file() else {}
    return {
        "version": version,
        "release_id": str(identity.get("release_id") or f"SAT-SIM-{version}"),
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser()
    parser.add_argument("--requirements", default="requirements-lock.txt")
    parser.add_argument("--aliases", default="configs/security/dependency_audit_aliases.json")
    parser.add_argument("--prior-report", default="reports/dependency_audit.json")
    parser.add_argument("--prior-raw", default="reports/security/pip_audit_raw.json")
    parser.add_argument("--prior-audit-requirements", default="reports/security/pip_audit_raw_requirements.txt")
    parser.add_argument("--max-age-hours", type=float, default=72.0)
    parser.add_argument("--output", default="reports/security/dependency_security_gate.json")
    args = parser.parse_args()

    paths = {
        "requirements": root / args.requirements,
        "aliases": root / args.aliases,
        "prior_report": root / args.prior_report,
        "prior_raw": root / args.prior_raw,
        "prior_audit_requirements": root / args.prior_audit_requirements,
    }
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    findings: list[dict[str, Any]] = []
    for name, path in paths.items():
        if not path.is_file():
            findings.append({"code": "SECURITY_EVIDENCE_MISSING", "item": name, "path": str(path.relative_to(root))})

    now = _utc_now()
    prior_report: dict[str, Any] = {}
    prior_raw: Any = {}
    current_dependencies: dict[str, str] = {}
    prior_declared_dependencies: dict[str, str] = {}
    raw_dependencies: dict[str, str] = {}
    audit_age_hours: float | None = None
    vulnerability_count = 0

    if not findings:
        try:
            prior_report = json.loads(paths["prior_report"].read_text(encoding="utf-8"))
            prior_raw = json.loads(paths["prior_raw"].read_text(encoding="utf-8"))
            current_dependencies = _current_audit_dependencies(paths["requirements"], paths["aliases"])
            prior_declared_dependencies = _parse_pinned(
                paths["prior_audit_requirements"].read_text(encoding="utf-8").splitlines()
            )
            raw_dependencies = _raw_dependencies(prior_raw)
            vulnerability_count = _vulnerability_count(prior_raw)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            findings.append({"code": "SECURITY_EVIDENCE_PARSE_FAILED", "detail": str(exc)})

    if not findings:
        if prior_report.get("status") != "PASS" or prior_report.get("return_code") != 0:
            findings.append({"code": "PRIOR_AUDIT_NOT_PASS", "status": prior_report.get("status"), "return_code": prior_report.get("return_code")})
        if int(prior_report.get("dependency_count") or 0) <= 0:
            findings.append({"code": "PRIOR_AUDIT_EMPTY_DEPENDENCY_SET"})
        if vulnerability_count != int(prior_report.get("vulnerability_count") or 0):
            findings.append({"code": "PRIOR_AUDIT_COUNT_MISMATCH", "raw": vulnerability_count, "report": prior_report.get("vulnerability_count")})
        if vulnerability_count:
            findings.append({"code": "PRIOR_AUDIT_HAS_VULNERABILITIES", "count": vulnerability_count})
        if current_dependencies != prior_declared_dependencies:
            findings.append({"code": "DEPENDENCY_SET_CHANGED_FROM_PRIOR_AUDIT", "current_count": len(current_dependencies), "prior_count": len(prior_declared_dependencies)})
        if current_dependencies != raw_dependencies:
            findings.append({"code": "PRIOR_RAW_DEPENDENCY_SET_MISMATCH", "current_count": len(current_dependencies), "raw_count": len(raw_dependencies)})
        try:
            generated = datetime.fromisoformat(str(prior_report["generated_at_utc"]).replace("Z", "+00:00"))
            audit_age_hours = (now - generated.astimezone(timezone.utc)).total_seconds() / 3600.0
            if audit_age_hours < 0:
                findings.append({"code": "PRIOR_AUDIT_FROM_FUTURE", "age_hours": audit_age_hours})
            elif audit_age_hours > float(args.max_age_hours):
                findings.append({"code": "PRIOR_AUDIT_STALE", "age_hours": audit_age_hours, "max_age_hours": args.max_age_hours})
        except (KeyError, TypeError, ValueError) as exc:
            findings.append({"code": "PRIOR_AUDIT_TIMESTAMP_INVALID", "detail": str(exc)})

    passed = not findings
    identity = _release_identity(root)
    valid_until_utc = None
    if prior_report.get("generated_at_utc"):
        try:
            prior_generated = datetime.fromisoformat(str(prior_report["generated_at_utc"]).replace("Z", "+00:00"))
            valid_until_utc = (prior_generated.astimezone(timezone.utc) + timedelta(hours=float(args.max_age_hours))).isoformat()
        except (TypeError, ValueError):
            valid_until_utc = None

    report = {
        "schema_version": "sat-sim.dependency-security-gate.v1",
        "release_id": identity["release_id"],
        "release_version": identity["version"],
        "generated_at_utc": now.isoformat(),
        "status": "PASS_INHERITED_UNCHANGED_DEPENDENCY_SET" if passed else "PENDING_FRESH_AUDIT_REQUIRED",
        "fresh_scan_executed": False,
        "inherited_audit": passed,
        "inheritance_policy": {
            "maximum_age_hours": float(args.max_age_hours),
            "requires_exact_dependency_set_match": True,
            "requires_prior_return_code_zero": True,
            "requires_prior_dependency_count_positive": True,
            "requires_zero_prior_vulnerabilities": True,
            "boundary": "This proves the release dependency set is identical to a recent successful audit; it is not a newly queried vulnerability database result.",
        },
        "dependency_count": len(current_dependencies),
        "vulnerability_count_at_prior_audit": vulnerability_count,
        "prior_audit_generated_at_utc": prior_report.get("generated_at_utc"),
        "prior_audit_age_hours": audit_age_hours,
        "valid_until_utc": valid_until_utc,
        "evidence": {
            name: {
                "path": str(path.relative_to(root)),
                "sha256": _sha256(path) if path.is_file() else None,
            }
            for name, path in paths.items()
        },
        "dependency_set_sha256": hashlib.sha256(
            json.dumps(current_dependencies, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest() if current_dependencies else None,
        "findings": findings,
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": report["status"], "dependency_count": report["dependency_count"], "findings": len(findings)}, ensure_ascii=False))
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
