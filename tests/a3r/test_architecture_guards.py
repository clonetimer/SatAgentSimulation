from __future__ import annotations

import ast
from pathlib import Path

from sat_sim.capability_registry import active_capability_ids, list_capabilities
from sat_sim.unified_execution import create_default_execution_registry


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_engine_independent_asset_packages_do_not_import_sat_sim_or_basilisk() -> None:
    for package in ("sat_sim_model_assets", "sat_sim_operations", "sat_sim_projection"):
        for path in Path("src", package).rglob("*.py"):
            imports = _imports(path)
            forbidden = sorted(name for name in imports if name == "sat_sim" or name.startswith("sat_sim.") or name == "Basilisk" or name.startswith("Basilisk."))
            assert not forbidden, f"{path}: {forbidden}"


def test_execution_registry_uses_logical_a3_key() -> None:
    registry = create_default_execution_registry()
    assert "basilisk.attitude_control_graph" in registry.keys()
    assert all(":" not in key and "/" not in key and "\\" not in key for key in registry.keys())


def test_capability_catalog_count_is_not_expanded_by_a3r() -> None:
    assert len(list_capabilities()) == 60
    assert "whole_spacecraft.unified_native.v1" in active_capability_ids()


def test_legacy_runner_has_one_production_call_site() -> None:
    hits: list[str] = []
    for path in Path("src", "sat_sim").rglob("*.py"):
        if path.name == "task_runner.py":
            continue
        text = path.read_text(encoding="utf-8")
        if "run_compiled_task(" in text:
            hits.append(path.as_posix())
    assert hits == ["src/sat_sim/legacy_execution/runner_bridge.py"]
