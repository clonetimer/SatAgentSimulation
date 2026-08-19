from __future__ import annotations

import ast
import json
import os
from pathlib import Path
import subprocess
import sys

import sat_sim_execution


def test_execution_contract_sources_only_depend_on_stdlib_kernel_or_relative_modules() -> None:
    root = Path(sat_sim_execution.__file__).resolve().parent
    allowed_stdlib = set(sys.stdlib_module_names)
    violations: list[str] = []
    for path in sorted(root.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
                level = 0
            elif isinstance(node, ast.ImportFrom):
                modules = [node.module or ""]
                level = node.level
            else:
                continue
            if level > 0:
                continue
            for module in modules:
                top = module.split(".", 1)[0]
                if top not in allowed_stdlib and top != "sat_sim_kernel":
                    violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []


def test_importing_execution_contract_does_not_import_product_or_basilisk() -> None:
    src_root = Path(sat_sim_execution.__file__).resolve().parents[1]
    code = (
        "import json,sys; import sat_sim_execution; "
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name == 'sat_sim' or name.startswith('sat_sim.') or name == 'Basilisk' or name.startswith('Basilisk.'))))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_root)
    completed = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, env=env)
    assert json.loads(completed.stdout) == []


def test_only_legacy_bridge_calls_run_compiled_task_in_production_source() -> None:
    src_root = Path(__file__).resolve().parents[2] / "src" / "sat_sim"
    calls: list[str] = []
    for path in src_root.rglob("*.py"):
        if path.name == "task_runner.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.id if isinstance(func, ast.Name) else func.attr if isinstance(func, ast.Attribute) else ""
            if name == "run_compiled_task":
                calls.append(f"{path.relative_to(src_root).as_posix()}:{node.lineno}")
    assert calls == ["legacy_execution/runner_bridge.py:31"]
