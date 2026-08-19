from __future__ import annotations

import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _module(root: Path):
    path = root / "scripts" / "build_dependency_security_gate.py"
    spec = importlib.util.spec_from_file_location("dependency_security_gate_test", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_dependency_security_gate_normalizes_names_and_requires_exact_pins() -> None:
    module = _module(Path(__file__).resolve().parents[1])
    assert module._parse_pinned(["PyYAML==6.0.3", "bsk==2.11.0"]) == {
        "pyyaml": "6.0.3",
        "bsk": "2.11.0",
    }
    try:
        module._parse_pinned(["requests>=2"])
    except ValueError as exc:
        assert "exactly pinned" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unpinned dependency was accepted")


def test_dependency_security_gate_current_project_evidence_is_within_policy() -> None:
    root = Path(__file__).resolve().parents[1]
    report = json.loads((root / "reports/dependency_audit.json").read_text())
    generated = datetime.fromisoformat(report["generated_at_utc"].replace("Z", "+00:00"))
    assert datetime.now(timezone.utc) - generated < timedelta(hours=72)

    module = _module(root)
    current = module._current_audit_dependencies(
        root / "requirements-lock.txt",
        root / "configs/security/dependency_audit_aliases.json",
    )
    prior = module._parse_pinned(
        (root / "reports/security/pip_audit_raw_requirements.txt").read_text().splitlines()
    )
    raw = module._raw_dependencies(
        json.loads((root / "reports/security/pip_audit_raw.json").read_text())
    )
    assert current == prior == raw
    assert len(current) == report["dependency_count"]
