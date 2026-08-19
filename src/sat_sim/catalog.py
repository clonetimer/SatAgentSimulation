"""Import-safe TaskSpec target catalog.

The catalog is used by validators, CLI tools, and Agents to understand which
component/subsystem runner modules exist without importing those runner modules.
This matters because some current runners depend on Basilisk at import time.
"""
from __future__ import annotations

import ast
from functools import lru_cache
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class TargetCapability:
    """Discovered capability for one component/subsystem runner."""

    task_type: str
    name: str
    runner_module: str
    runner_file: str
    functions: tuple[str, ...] = field(default_factory=tuple)

    @property
    def supports_nominal(self) -> bool:
        return "run_nominal_case" in self.functions

    @property
    def supports_degradation(self) -> bool:
        return "run_degradation_case" in self.functions

    @property
    def supports_fault(self) -> bool:
        return "run_fault_case" in self.functions

    @property
    def supports_save_nominal(self) -> bool:
        return "run_and_save_nominal_case" in self.functions

    @property
    def supports_backend(self) -> bool:
        return "run_backend" in self.functions

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.update(
            {
                "supports_nominal": self.supports_nominal,
                "supports_degradation": self.supports_degradation,
                "supports_fault": self.supports_fault,
                "supports_save_nominal": self.supports_save_nominal,
                "supports_backend": self.supports_backend,
            }
        )
        return payload


def _src_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _runner_functions(path: Path) -> tuple[str, ...]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except Exception:
        return tuple()
    names: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            names.append(node.name)
    return tuple(names)


def _discover_under(root: Path, package: str, task_type: str) -> list[TargetCapability]:
    out: list[TargetCapability] = []
    if not root.exists():
        return out
    for runner_path in sorted(root.glob("*/runner.py")):
        name = runner_path.parent.name
        functions = _runner_functions(runner_path)
        if not functions:
            continue
        out.append(
            TargetCapability(
                task_type=task_type,
                name=name,
                runner_module=f"{package}.{name}.runner",
                runner_file=str(runner_path),
                functions=functions,
            )
        )
    return out


@lru_cache(maxsize=16)
def _discover_target_capabilities_cached(root_key: str) -> tuple[TargetCapability, ...]:
    root = Path(root_key)
    capabilities: list[TargetCapability] = []
    capabilities.extend(_discover_under(root / "components", "components", "component"))
    capabilities.extend(_discover_under(root / "subsystems", "subsystems", "subsystem"))
    return tuple(capabilities)


def clear_target_catalog_cache() -> None:
    """Clear the static runner catalog cache after source files are modified."""

    _discover_target_capabilities_cached.cache_clear()


def discover_target_capabilities(src_root: str | Path | None = None) -> tuple[TargetCapability, ...]:
    """Discover component/subsystem runner capabilities by a cached static AST scan."""

    root = (Path(src_root) if src_root is not None else _src_root()).resolve()
    return _discover_target_capabilities_cached(str(root))


def catalog_payload(src_root: str | Path | None = None) -> dict[str, Any]:
    """Return a JSON-serializable capability catalog."""

    capabilities = discover_target_capabilities(src_root)
    by_type: dict[str, list[dict[str, Any]]] = {"component": [], "subsystem": []}
    for cap in capabilities:
        by_type.setdefault(cap.task_type, []).append(cap.to_dict())
    return {
        "schema": "sat_sim.target_catalog.v0.1",
        "component_count": len(by_type.get("component", [])),
        "subsystem_count": len(by_type.get("subsystem", [])),
        "targets": by_type,
    }


def known_target_names(task_type: str, src_root: str | Path | None = None) -> tuple[str, ...]:
    """Return known target names for task_type='component' or 'subsystem'."""

    return tuple(cap.name for cap in discover_target_capabilities(src_root) if cap.task_type == task_type)


def get_target_capability(task_type: str, name: str, src_root: str | Path | None = None) -> TargetCapability | None:
    """Return capability for one target name, or None if absent."""

    for cap in discover_target_capabilities(src_root):
        if cap.task_type == task_type and cap.name == name:
            return cap
    return None


def runner_module_for(task_type: str, name: str, src_root: str | Path | None = None) -> str:
    """Return runner module path for a known target."""

    cap = get_target_capability(task_type, name, src_root)
    if cap is None:
        known = ", ".join(known_target_names(task_type, src_root))
        raise ValueError(f"unknown {task_type} target {name!r}; known targets: {known}")
    return cap.runner_module


__all__ = [
    "TargetCapability",
    "clear_target_catalog_cache",
    "catalog_payload",
    "discover_target_capabilities",
    "get_target_capability",
    "known_target_names",
    "runner_module_for",
]
