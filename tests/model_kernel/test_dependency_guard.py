from __future__ import annotations

import ast
from dataclasses import is_dataclass
import json
from pathlib import Path
import os
import subprocess
import sys

import sat_sim_kernel


def test_kernel_sources_only_import_stdlib_or_relative_modules() -> None:
    root = Path(sat_sim_kernel.__file__).resolve().parent
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
                if top not in allowed_stdlib:
                    violations.append(f"{path.name}:{node.lineno}:{module}")
    assert violations == []


def test_importing_kernel_does_not_import_product_or_engine_packages() -> None:
    src_root = Path(sat_sim_kernel.__file__).resolve().parents[1]
    code = (
        "import json,sys; import sat_sim_kernel; "
        "print(json.dumps(sorted(name for name in sys.modules "
        "if name == 'sat_sim' or name.startswith('sat_sim.') or name == 'Basilisk' or name.startswith('Basilisk.'))))"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = str(src_root)
    completed = subprocess.run([sys.executable, "-c", code], check=True, capture_output=True, text=True, env=env)
    assert json.loads(completed.stdout) == []


def test_all_public_kernel_dataclasses_are_frozen() -> None:
    mutable: list[str] = []
    for name in sat_sim_kernel.__all__:
        value = getattr(sat_sim_kernel, name)
        if isinstance(value, type) and is_dataclass(value):
            params = getattr(value, "__dataclass_params__", None)
            if params is None or not params.frozen:
                mutable.append(name)
    assert mutable == []
