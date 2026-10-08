#!/usr/bin/env python3
"""
用途：检查脚本目录、脚本头部、命名、重复脚本和依赖分层是否符合项目规范。
参数：--output 指定机器可读报告路径；--strict 在发现警告时也返回非零退出码。
输出：生成脚本与依赖治理 JSON 报告，并以退出码表示门禁结果。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10
    import tomli as tomllib

SCRIPT_SUFFIXES = {".py", ".sh", ".ps1", ".bat", ".cmd"}
EXEMPT_TOP_LEVEL = {
    "src",
    "tests",
    "scripts",
    "third_party",
    ".github",
    "generated",
    "generated_tasks",
    "runs",
    "reports",
    "build",
    "dist",
    ".venv",
    "venv",
    ".sat_sim_api",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
}
BAD_NAME_PATTERNS = (
    re.compile(r"(^|[_-])(temp|tmp|new|copy|backup|bak)([_-]|\.|$)", re.I),
    re.compile(r"final[_-]final", re.I),
    re.compile(r"test\d+", re.I),
)
REQUIRED_HEADER_MARKERS = ("用途", "参数", "输出")
PINNED_BOOTSTRAP_TOOLS = {
    "pip": "26.2.1",
    "setuptools": "83.0.0",
    "wheel": "0.46.2",
}
BOOTSTRAP_PIN_TARGETS = (
    "scripts/bootstrap_local.sh",
    "scripts/bootstrap_windows.ps1",
    ".github/workflows/dependency-audit.yml",
)
STANDALONE_GOVERNANCE_SCRIPTS = (
    "scripts/check_script_dependency_governance.py",
    "scripts/scan_dependencies.py",
)
DEV_ONLY_PACKAGES = {
    "build",
    "pip-audit",
    "playwright",
    "pytest",
    "ruff",
}


def _release_version(root: Path) -> str:
    """Read the release version without importing the production package."""
    pyproject_path = root / "pyproject.toml"
    payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    return str(payload["project"]["version"])


def _issue(items: list[dict[str, Any]], code: str, path: str, detail: str, severity: str = "ERROR") -> None:
    items.append({"severity": severity, "code": code, "path": path, "detail": detail})


def _script_header_ok(path: Path) -> bool:
    try:
        text = path.read_text(encoding="utf-8")[:1600]
    except UnicodeDecodeError:
        return False
    return all(marker in text for marker in REQUIRED_HEADER_MARKERS)


def _iter_dependency_strings(pyproject: dict[str, Any]) -> list[tuple[str, str]]:
    values: list[tuple[str, str]] = []
    build = pyproject.get("build-system", {}).get("requires", [])
    values.extend(("build-system.requires", value) for value in build)
    project = pyproject.get("project", {})
    values.extend(("project.dependencies", value) for value in project.get("dependencies", []))
    for group, deps in project.get("optional-dependencies", {}).items():
        values.extend((f"project.optional-dependencies.{group}", value) for value in deps)
    return values


def _requirement_name(value: str) -> str:
    requirement = value.split(";", 1)[0].strip()
    requirement = requirement.split(" @ ", 1)[0]
    return re.split(r"[<>=~!\[]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def _is_exact_requirement(value: str) -> bool:
    requirement = value.split(";", 1)[0].strip()
    if requirement.startswith(("-r ", "--requirement ")):
        return True
    if " @ " in requirement:
        return True
    return "==" in requirement and "*" not in requirement


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(root: Path) -> dict[str, Any]:
    issues: list[dict[str, Any]] = []
    scripts_root = root / "scripts"
    if not scripts_root.is_dir():
        _issue(issues, "SCRIPTS_DIRECTORY_MISSING", "scripts", "根目录缺少 scripts 文件夹。")
    if (root / "tools").exists():
        _issue(issues, "LEGACY_TOOLS_DIRECTORY_PRESENT", "tools", "脚本仍散落在 tools 目录。")

    outside_scripts: list[str] = []
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in SCRIPT_SUFFIXES:
            continue
        relative = path.relative_to(root)
        if relative.parts[0] in EXEMPT_TOP_LEVEL:
            continue
        outside_scripts.append(relative.as_posix())
        _issue(issues, "NON_PRODUCTION_SCRIPT_OUTSIDE_SCRIPTS", relative.as_posix(), "非生产脚本必须迁移到 scripts。")

    script_files: list[str] = []
    script_hashes: dict[str, str] = {}
    if scripts_root.exists():
        for path in scripts_root.rglob("*"):
            if not path.is_file() or path.suffix.lower() not in SCRIPT_SUFFIXES:
                continue
            relative = path.relative_to(root).as_posix()
            script_files.append(relative)
            if not _script_header_ok(path):
                _issue(issues, "SCRIPT_HEADER_INCOMPLETE", relative, "文件头必须同时说明用途、参数和输出。")
            if any(pattern.search(path.name) for pattern in BAD_NAME_PATTERNS):
                _issue(issues, "NON_SEMANTIC_SCRIPT_NAME", relative, "脚本名称疑似临时、备份或非语义化名称。")
            if "deprecated" in path.parts:
                text = path.read_text(encoding="utf-8", errors="replace")[:2000]
                if "替代" not in text or "废弃" not in text:
                    _issue(issues, "DEPRECATED_SCRIPT_WITHOUT_RATIONALE", relative, "废弃脚本必须说明替代项和废弃原因。")
            digest = _sha256(path)
            if digest in script_hashes:
                _issue(
                    issues,
                    "DUPLICATE_SCRIPT_CONTENT",
                    relative,
                    f"与 {script_hashes[digest]} 内容完全相同，应合并或说明保留理由。",
                )
            else:
                script_hashes[digest] = relative

    inventory_path = scripts_root / "SCRIPT_INVENTORY.md"
    if not inventory_path.is_file():
        _issue(issues, "SCRIPT_INVENTORY_MISSING", "scripts/SCRIPT_INVENTORY.md", "缺少脚本资产清单与交叉检查结论。")
    else:
        inventory_text = inventory_path.read_text(encoding="utf-8")
        for relative in script_files:
            script_name = Path(relative).name
            if f"`{script_name}`" not in inventory_text:
                _issue(issues, "SCRIPT_NOT_IN_INVENTORY", relative, "脚本未登记到 SCRIPT_INVENTORY.md。")

    for relative in STANDALONE_GOVERNANCE_SCRIPTS:
        path = root / relative
        if not path.is_file():
            _issue(issues, "STANDALONE_GOVERNANCE_SCRIPT_MISSING", relative, "缺少独立治理脚本。")
            continue
        text = path.read_text(encoding="utf-8")
        if re.search(r"(^|\n)\s*(from\s+sat_sim|import\s+sat_sim)", text):
            _issue(
                issues,
                "GOVERNANCE_SCRIPT_IMPORTS_PRODUCTION_PACKAGE",
                relative,
                "治理脚本必须能在仅安装审计工具的洁净环境中运行，不能导入 sat_sim。",
            )

    for target in BOOTSTRAP_PIN_TARGETS:
        target_path = root / target
        if not target_path.is_file():
            _issue(issues, "BOOTSTRAP_PIN_TARGET_MISSING", target, "缺少需要执行精确工具版本检查的文件。")
            continue
        target_text = target_path.read_text(encoding="utf-8")
        for package, version in PINNED_BOOTSTRAP_TOOLS.items():
            exact = f"{package}=={version}"
            if package in {"setuptools", "wheel"} and target.endswith("dependency-audit.yml"):
                continue
            if exact not in target_text:
                _issue(issues, "BOOTSTRAP_TOOL_NOT_EXACTLY_PINNED", target, f"启动/审计命令必须显式使用 {exact}。")

    required_dependency_files = [
        "requirements.txt",
        "requirements-api.txt",
        "requirements-dev.txt",
        "requirements-ui-test.txt",
        "requirements-lock.txt",
        "constraints.txt",
    ]
    for name in required_dependency_files:
        if not (root / name).is_file():
            _issue(issues, "DEPENDENCY_FILE_MISSING", name, "缺少分层或锁定依赖文件。")

    pyproject_path = root / "pyproject.toml"
    if pyproject_path.is_file():
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
        for group, value in _iter_dependency_strings(pyproject):
            if "*" in value:
                _issue(issues, "DEPENDENCY_WILDCARD", "pyproject.toml", f"{group}: {value}")
            if not _is_exact_requirement(value):
                _issue(issues, "DEPENDENCY_NOT_EXACT", "pyproject.toml", f"{group}: {value}")
        project = pyproject.get("project", {})
        optional = project.get("optional-dependencies", {})
        for required_group in ("api", "dev", "ui-test"):
            if required_group not in optional:
                _issue(issues, "DEPENDENCY_GROUP_MISSING", "pyproject.toml", f"缺少 optional-dependencies.{required_group}。")
        runtime_names = {_requirement_name(value) for value in project.get("dependencies", [])}
        misplaced = sorted(runtime_names & DEV_ONLY_PACKAGES)
        for name in misplaced:
            _issue(issues, "DEV_DEPENDENCY_IN_RUNTIME", "pyproject.toml", f"{name} 应位于开发或测试依赖组，而不是生产 dependencies。")
    else:
        _issue(issues, "PYPROJECT_MISSING", "pyproject.toml", "缺少项目依赖定义。")

    for name in required_dependency_files:
        path = root / name
        if not path.is_file():
            continue
        for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            line = raw.strip()
            if not line or line.startswith("#") or line.startswith(("-r ", "--requirement ")):
                continue
            if "*" in line:
                _issue(issues, "DEPENDENCY_WILDCARD", name, f"第 {line_number} 行: {line}")
            if not _is_exact_requirement(line):
                _issue(issues, "DEPENDENCY_NOT_EXACT", name, f"第 {line_number} 行: {line}")

    weekly_workflow = root / ".github" / "workflows" / "dependency-audit.yml"
    if not weekly_workflow.is_file():
        _issue(issues, "WEEKLY_AUDIT_WORKFLOW_MISSING", weekly_workflow.relative_to(root).as_posix(), "缺少每周依赖漏洞扫描工作流。")
    else:
        text = weekly_workflow.read_text(encoding="utf-8")
        required_markers = ("schedule:", "cron:", "scan_dependencies.py", "--fail-on-vulnerability")
        if not all(marker in text for marker in required_markers):
            _issue(issues, "WEEKLY_AUDIT_WORKFLOW_INCOMPLETE", weekly_workflow.relative_to(root).as_posix(), "工作流必须包含周期计划、统一扫描脚本和漏洞阻断参数。")

    cr_workflow = root / ".github" / "workflows" / "project-governance.yml"
    if not cr_workflow.is_file():
        _issue(issues, "CR_GOVERNANCE_WORKFLOW_MISSING", cr_workflow.relative_to(root).as_posix(), "缺少面向代码评审的脚本与依赖治理门禁。")
    else:
        text = cr_workflow.read_text(encoding="utf-8")
        if "pull_request:" not in text or "check_script_dependency_governance.py" not in text or "--strict" not in text:
            _issue(issues, "CR_GOVERNANCE_WORKFLOW_INCOMPLETE", cr_workflow.relative_to(root).as_posix(), "CR 工作流必须在 pull_request 上执行严格治理检查。")

    error_count = sum(item["severity"] == "ERROR" for item in issues)
    warning_count = sum(item["severity"] == "WARNING" for item in issues)
    return {
        "schema_version": "sat-sim.script-dependency-governance.v2",
        "release_version": _release_version(root),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "PASS" if error_count == 0 else "FAIL",
        "summary": {
            "script_count": len(script_files),
            "outside_script_count": len(outside_scripts),
            "duplicate_script_count": sum(item["code"] == "DUPLICATE_SCRIPT_CONTENT" for item in issues),
            "error_count": error_count,
            "warning_count": warning_count,
        },
        "scripts": script_files,
        "issues": issues,
    }


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    release_version = _release_version(root)
    parser = argparse.ArgumentParser(description="Check script and dependency governance")
    parser.add_argument("--output", default=f"reports/v{release_version}_script_dependency_governance.json")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args()
    report = run(root)
    output = root / args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report["summary"], ensure_ascii=False))
    if report["status"] != "PASS":
        return 1
    if args.strict and report["summary"]["warning_count"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
