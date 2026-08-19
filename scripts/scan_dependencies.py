#!/usr/bin/env python3
"""
用途：调用 pip-audit 对锁定依赖进行漏洞扫描，并生成稳定的项目级机器可读报告。
参数：--requirements、--output、--raw-output、--service、--timeout-seconds、--fail-on-vulnerability。
输出：生成封装后的扫描报告和 pip-audit 原始 JSON；可按策略返回非零退出码。
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

NETWORK_ERROR_MARKERS = (
    "Temporary failure in name resolution",
    "NameResolutionError",
    "ConnectionError",
    "Network is unreachable",
    "MaxRetryError",
)
TOOL_MISSING_MARKERS = (
    "No module named pip_audit",
    "No module named 'pip_audit'",
)


def _release_version(root: Path) -> str:
    """Read the release version without importing the production package."""
    payload = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _dependencies(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        values = payload.get("dependencies", [])
    elif isinstance(payload, list):
        values = payload
    else:
        values = []
    return [item for item in values if isinstance(item, dict)]


def _count_vulnerabilities(payload: Any) -> int:
    total = 0
    for item in _dependencies(payload):
        vulnerabilities = item.get("vulns") or item.get("vulnerabilities") or []
        total += len(vulnerabilities)
    return total


def _load_audit_aliases(root: Path) -> list[dict[str, str]]:
    """Load transparent aliases for metadata-only local compatibility builds."""
    path = root / "configs" / "security" / "dependency_audit_aliases.json"
    if not path.is_file():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    aliases = payload.get("aliases", []) if isinstance(payload, dict) else []
    result: list[dict[str, str]] = []
    for item in aliases:
        if not isinstance(item, dict):
            continue
        installed = str(item.get("installed_requirement", "")).strip()
        audit_as = str(item.get("audit_requirement", "")).strip()
        if installed and audit_as:
            result.append({
                "installed_requirement": installed,
                "audit_requirement": audit_as,
                "reason": str(item.get("reason", "")).strip(),
                "evidence": str(item.get("evidence", "")).strip(),
            })
    return result


def _prepare_audit_requirements(
    requirements: Path, audit_requirements: Path, aliases: list[dict[str, str]]
) -> list[dict[str, str]]:
    """Create the externally auditable requirement set and record each substitution."""
    alias_map = {item["installed_requirement"].lower(): item for item in aliases}
    applied: list[dict[str, str]] = []
    output_lines: list[str] = []
    for line in requirements.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        item = alias_map.get(stripped.lower())
        if item is None:
            output_lines.append(line)
            continue
        output_lines.append(item["audit_requirement"])
        applied.append(item)
    audit_requirements.parent.mkdir(parents=True, exist_ok=True)
    audit_requirements.write_text("\n".join(output_lines) + "\n", encoding="utf-8")
    return applied


def _portable_path(path: Path, root: Path) -> str:
    """Represent project paths relatively and external paths without host layout."""
    resolved = path.resolve()
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        return f"$EXTERNAL/{resolved.name}"


def _portable_command_arg(value: str, root: Path) -> str:
    text = str(value)
    root_text = str(root.resolve())
    if root_text in text:
        return text.replace(root_text, "<PROJECT_ROOT>").replace("\\", "/")
    try:
        candidate = Path(text)
        if candidate.is_absolute() or text.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:[\\/]", text):
            return f"$EXTERNAL/{candidate.name}"
    except (TypeError, ValueError):
        pass
    return text


def _classify(return_code: int | None, vulnerability_count: int, stderr: str) -> str:
    if return_code == 0 and vulnerability_count == 0:
        return "PASS"
    if vulnerability_count > 0:
        return "VULNERABILITIES_FOUND"
    if any(marker in stderr for marker in TOOL_MISSING_MARKERS):
        return "NOT_EXECUTED_TOOL_MISSING"
    if any(marker in stderr for marker in NETWORK_ERROR_MARKERS):
        return "NOT_EXECUTED_NETWORK_UNREACHABLE"
    return "ERROR"


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    release_version = _release_version(root)
    parser = argparse.ArgumentParser(description="Run a reproducible dependency vulnerability audit")
    parser.add_argument("--requirements", default="requirements-lock.txt")
    parser.add_argument("--output", default="reports/security/dependency_audit_report.json")
    parser.add_argument("--raw-output", default="reports/security/pip_audit_raw.json")
    parser.add_argument("--service", choices=("pypi", "osv"), default="pypi")
    parser.add_argument("--timeout-seconds", type=int, default=20)
    parser.add_argument("--fail-on-vulnerability", action="store_true")
    args = parser.parse_args()

    requirements = root / args.requirements
    output = root / args.output
    raw_output = root / args.raw_output
    output.parent.mkdir(parents=True, exist_ok=True)
    raw_output.parent.mkdir(parents=True, exist_ok=True)

    if not requirements.is_file():
        report = {
            "schema_version": "sat-sim.dependency-audit.v2",
            "release_version": release_version,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "NOT_EXECUTED_REQUIREMENTS_MISSING",
            "requirements": args.requirements,
            "tool": "pip-audit",
            "command": [],
            "return_code": None,
            "dependency_count": 0,
            "vulnerability_count": 0,
            "raw_output": args.raw_output,
            "stdout": "",
            "stderr": f"Requirements file not found: {args.requirements}",
        }
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        print(json.dumps({"status": report["status"], "vulnerability_count": 0}, ensure_ascii=False))
        return 2

    aliases = _load_audit_aliases(root)
    audit_requirements = raw_output.with_name(f"{raw_output.stem}_requirements.txt")
    aliases_applied = _prepare_audit_requirements(requirements, audit_requirements, aliases)

    command = [
        sys.executable,
        "-m",
        "pip_audit",
        "--requirement",
        str(audit_requirements),
        "--format",
        "json",
        "--vulnerability-service",
        args.service,
        "--strict",
        "--no-deps",
        "--disable-pip",
        "--progress-spinner",
        "off",
        "--timeout",
        str(max(args.timeout_seconds, 1)),
        "--output",
        str(raw_output),
    ]
    try:
        completed = subprocess.run(command, cwd=root, text=True, capture_output=True, check=False)
        return_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except OSError as exc:
        return_code = None
        stdout = ""
        stderr = str(exc)

    raw_payload: Any = {}
    if raw_output.exists():
        try:
            raw_payload = json.loads(raw_output.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw_payload = {}
    dependency_count = len(_dependencies(raw_payload))
    vulnerability_count = _count_vulnerabilities(raw_payload)
    status = _classify(return_code, vulnerability_count, stderr)

    python_env = str(Path(sys.executable).absolute().parents[1])
    sanitized_stdout = stdout.replace(str(root), "<PROJECT_ROOT>").replace(python_env, "<PYTHON_ENV>")
    sanitized_stderr = stderr.replace(str(root), "<PROJECT_ROOT>").replace(python_env, "<PYTHON_ENV>")
    display_command = ["python", *command[1:]]
    display_command = [_portable_command_arg(item, root) for item in display_command]

    report = {
        "schema_version": "sat-sim.dependency-audit.v2",
        "release_version": release_version,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "requirements": requirements.relative_to(root).as_posix(),
        "audit_requirements": _portable_path(audit_requirements, root),
        "aliases_applied": aliases_applied,
        "tool": "pip-audit",
        "vulnerability_service": args.service,
        "command": display_command,
        "return_code": return_code,
        "dependency_count": dependency_count,
        "vulnerability_count": vulnerability_count,
        "raw_output": _portable_path(raw_output, root),
        "stdout": sanitized_stdout[-4000:],
        "stderr": sanitized_stderr[-4000:],
    }
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"status": status, "vulnerability_count": vulnerability_count}, ensure_ascii=False))

    if status in {
        "NOT_EXECUTED_REQUIREMENTS_MISSING",
        "NOT_EXECUTED_TOOL_MISSING",
        "NOT_EXECUTED_NETWORK_UNREACHABLE",
        "ERROR",
    }:
        return 2
    if args.fail_on_vulnerability and vulnerability_count:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
