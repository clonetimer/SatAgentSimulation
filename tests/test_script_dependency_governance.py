from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def _load_governance_module():
    path = ROOT / "scripts" / "check_script_dependency_governance.py"
    spec = importlib.util.spec_from_file_location("script_dependency_governance", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_non_production_scripts_are_consolidated_under_scripts() -> None:
    assert not (ROOT / "tools").exists()
    module = _load_governance_module()
    report = module.run(ROOT)
    assert report["status"] == "PASS", report["issues"]
    assert report["summary"]["outside_script_count"] == 0
    assert report["summary"]["error_count"] == 0
    assert report["summary"]["script_count"] >= 29


def test_script_inventory_and_deprecated_policy_are_present() -> None:
    inventory = (ROOT / "scripts" / "SCRIPT_INVENTORY.md").read_text(encoding="utf-8")
    assert "交叉检查结论" in inventory
    assert "start_local_windows.ps1" in inventory
    assert "start_windows.ps1" in inventory
    assert "run_coupling_completeness_gate.py" in inventory
    assert (ROOT / "scripts" / "deprecated" / "README.md").is_file()


def test_dependencies_are_separated_and_exactly_pinned() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert pyproject["project"]["version"] == "0.7.8"
    assert set(pyproject["project"]["optional-dependencies"]) >= {"api", "dev", "ui-test"}
    for requirement in pyproject["build-system"]["requires"] + pyproject["project"]["dependencies"]:
        assert "*" not in requirement
        assert "==" in requirement.split(";", 1)[0]
    for dependencies in pyproject["project"]["optional-dependencies"].values():
        for requirement in dependencies:
            assert "*" not in requirement
            assert "==" in requirement.split(";", 1)[0]
    for name in ("requirements.txt", "requirements-api.txt", "requirements-dev.txt", "requirements-ui-test.txt", "requirements-lock.txt"):
        assert (ROOT / name).is_file()
    assert "wheel==0.46.2" in pyproject["build-system"]["requires"]
    assert "wheel==0.46.2" in pyproject["project"]["optional-dependencies"]["dev"]
    assert "wheel==0.46.2" in (ROOT / "requirements-lock.txt").read_text(encoding="utf-8")


def test_bootstrap_and_weekly_audit_tools_are_exactly_pinned() -> None:
    expected = {
        "scripts/bootstrap_local.sh": ("pip==26.2.1", "setuptools==83.0.0", "wheel==0.46.2"),
        "scripts/bootstrap_windows.ps1": ("pip==26.2.1", "setuptools==83.0.0", "wheel==0.46.2"),
        ".github/workflows/dependency-audit.yml": ("pip==26.2.1", "pip-audit==2.10.1"),
    }
    for relative, pins in expected.items():
        text = (ROOT / relative).read_text(encoding="utf-8")
        for pin in pins:
            assert pin in text, (relative, pin)


def test_weekly_dependency_audit_is_configured() -> None:
    workflow = (ROOT / ".github" / "workflows" / "dependency-audit.yml").read_text(encoding="utf-8")
    assert "schedule:" in workflow
    assert "cron:" in workflow
    assert "scripts/scan_dependencies.py" in workflow
    assert "--fail-on-vulnerability" in workflow
    assert "check_script_dependency_governance.py" in workflow


def test_pull_request_governance_gate_is_configured() -> None:
    workflow = (ROOT / ".github" / "workflows" / "project-governance.yml").read_text(encoding="utf-8")
    assert "pull_request:" in workflow
    assert "check_script_dependency_governance.py" in workflow
    assert "--strict" in workflow


def test_governance_scripts_are_standalone_and_scanner_uses_lock_without_resolution(tmp_path: Path) -> None:
    for relative in (
        "scripts/check_script_dependency_governance.py",
        "scripts/scan_dependencies.py",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "from sat_sim" not in text
        assert "import sat_sim" not in text

    scanner = (ROOT / "scripts" / "scan_dependencies.py").read_text(encoding="utf-8")
    assert '"--no-deps"' in scanner
    assert '"--disable-pip"' in scanner

    output = tmp_path / "governance.json"
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "check_script_dependency_governance.py"), "--strict", "--output", str(output)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert json.loads(output.read_text(encoding="utf-8"))["status"] == "PASS"


def test_current_release_manifest_records_governance_evidence() -> None:
    manifest = json.loads((ROOT / "src/sat_sim/release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["release_id"] == "SAT-SIM-0.7.8-ENGINEERING-BASELINE"
    assert manifest["release_version"] == "0.7.8"
    for required in (
        "scripts/SCRIPT_INVENTORY.md",
        ".github/workflows/dependency-audit.yml",
        "reports/script_governance.json",
        "reports/dependency_audit.json",
        ".github/workflows/project-governance.yml",
    ):
        assert required in manifest["required_documents"]
