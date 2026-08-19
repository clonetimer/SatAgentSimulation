from __future__ import annotations

from pathlib import Path
import importlib.util
import json
import tomllib

from sat_sim.release_closure import RELEASE_VERSION

ROOT = Path(__file__).resolve().parents[1]

def _load_script_module(name: str):
    path = ROOT / "scripts" / name
    spec = importlib.util.spec_from_file_location(f"test_{path.stem}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module



def _pins(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_v0575_release_identity_and_security_pins() -> None:
    data = tomllib.loads(_pins("pyproject.toml"))
    assert data["project"]["version"] == RELEASE_VERSION
    assert "setuptools==83.0.0" in data["build-system"]["requires"]
    assert "wheel==0.46.2" in data["build-system"]["requires"]
    assert "fastapi==0.139.2" in data["project"]["optional-dependencies"]["api"]
    assert "starlette==1.3.1" in data["project"]["optional-dependencies"]["api"]
    assert "pytest==9.0.3" in data["project"]["optional-dependencies"]["dev"]
    assert "wheel==0.46.2" in data["project"]["optional-dependencies"]["dev"]


def test_vulnerable_versions_are_absent_from_current_dependency_inputs() -> None:
    current_files = (
        "pyproject.toml",
        "constraints.txt",
        "requirements-api.txt",
        "requirements-dev.txt",
        "requirements-lock.txt",
        "scripts/bootstrap_local.sh",
        "scripts/bootstrap_windows.ps1",
        ".github/workflows/dependency-audit.yml",
    )
    combined = "\n".join(_pins(path) for path in current_files)
    for vulnerable in (
        "fastapi==0.128.2",
        "starlette==0.50.0",
        "pillow==12.2.0",
        "pip==25.2",
        "pytest==9.0.2",
        "setuptools==82.0.1",
        "wheel==0.45.1",
    ):
        assert vulnerable not in combined


def test_safe_versions_are_locked_and_constraints_cover_transitive_pillow() -> None:
    lock = _pins("requirements-lock.txt").lower()
    constraints = _pins("constraints.txt").lower()
    for pin in (
        "fastapi==0.139.2",
        "starlette==1.3.1",
        "pillow==12.3.0",
        "pip==26.1.2",
        "pytest==9.0.3",
        "setuptools==83.0.0",
        "wheel==0.46.2",
    ):
        assert pin in lock
    assert "pillow==12.3.0" in constraints


def test_dependency_audit_alias_preserves_upstream_bsk_identity() -> None:
    aliases = json.loads((ROOT / "configs/security/dependency_audit_aliases.json").read_text(encoding="utf-8"))
    [alias] = aliases["aliases"]
    assert alias["installed_requirement"] == "bsk==2.11.0+satfix1"
    assert alias["audit_requirement"] == "bsk==2.11.0"
    evidence = ROOT / alias["evidence"]
    assert evidence.is_file()
    policy = json.loads(evidence.read_text(encoding="utf-8"))
    assert policy["status"] == "PASS"
    assert policy["accepted_runtime_versions"] == ["2.11.0", "2.11.0+satfix1"]
    assert policy["runtime_verification"]["mode"] == "import_and_distribution_identity"
    assert policy["runtime_verification"]["offline_wheel_bundle_required"] is False
    assert policy["satfix1"]["kind"] == "metadata_only_compatibility_variant"


def test_dependency_scanner_emits_normalized_audit_requirements(tmp_path: Path) -> None:
    scanner = _load_script_module("scan_dependencies.py")
    aliases = scanner._load_audit_aliases(ROOT)
    source = tmp_path / "requirements.txt"
    normalized = tmp_path / "audit.txt"
    source.write_text("bsk==2.11.0+satfix1\npillow==12.3.0\n", encoding="utf-8")
    applied = scanner._prepare_audit_requirements(source, normalized, aliases)
    assert normalized.read_text(encoding="utf-8") == "bsk==2.11.0\npillow==12.3.0\n"
    assert len(applied) == 1
