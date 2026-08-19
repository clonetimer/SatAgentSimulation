from __future__ import annotations

from dataclasses import asdict, dataclass
from importlib import import_module
from inspect import signature
from pathlib import Path
from typing import Any

from components.battery.faults import BatteryFaultType
from components.fault_spec import FaultSpec


SRC_ROOT = Path(__file__).resolve().parent


@dataclass(frozen=True)
class EntitySpec:
    level: str
    name: str
    module_name: str
    nominal_fn: str | None = "run_nominal_case"
    all_modes_fn: str | None = "run_all_modes"


def _runner_specs(level: str, base_dir: Path) -> list[EntitySpec]:
    specs: list[EntitySpec] = []
    for runner in sorted(base_dir.rglob("runner.py")):
        parts = runner.with_suffix("").relative_to(SRC_ROOT).parts
        if any(part.startswith("__") for part in parts):
            continue
        module_name = ".".join(parts)
        relative_name = ".".join(parts[1:-1]) if len(parts) > 2 else parts[1]
        specs.append(EntitySpec(level=level, name=relative_name, module_name=module_name))
    return specs


def component_specs() -> list[EntitySpec]:
    return _runner_specs("component", SRC_ROOT / "components")


def subsystem_specs() -> list[EntitySpec]:
    return _runner_specs("subsystem", SRC_ROOT / "subsystems")


def whole_spacecraft_specs() -> list[EntitySpec]:
    return [
        EntitySpec(
            level="whole_spacecraft",
            name="whole_spacecraft",
            module_name="whole_spacecraft.runner",
            nominal_fn=None,
            all_modes_fn=None,
        )
    ]


def all_specs() -> list[EntitySpec]:
    return component_specs() + subsystem_specs() + whole_spacecraft_specs()


def load_module(spec: EntitySpec):
    return import_module(spec.module_name)


def has_fn(spec: EntitySpec, fn_name: str | None) -> bool:
    if fn_name is None:
        return False
    return hasattr(load_module(spec), fn_name)


def get_signature_text(spec: EntitySpec, fn_name: str | None) -> str:
    if not has_fn(spec, fn_name):
        return "-"
    fn = getattr(load_module(spec), fn_name)
    return f"{fn.__name__}{signature(fn)}"


def run_nominal(spec: EntitySpec) -> dict[str, Any]:
    if spec.level == "whole_spacecraft":
        from whole_spacecraft.runner import run_whole_spacecraft_native_case
        from whole_spacecraft.schemas import WholeSpacecraftRunConfig

        summary, rows = run_whole_spacecraft_native_case(WholeSpacecraftRunConfig(duration_s=60.0, sample_s=10.0))
        return {
            "summary": asdict(summary),
            "trace_fields": list(asdict(rows[0]).keys()) if rows else [],
            "sample_count": len(rows),
        }
    if spec.nominal_fn is None:
        raise AttributeError(f"{spec.module_name} has no nominal function")
    return getattr(load_module(spec), spec.nominal_fn)()


def run_all_modes(spec: EntitySpec) -> dict[str, Any]:
    if spec.level == "whole_spacecraft":
        from whole_spacecraft.runner import run_whole_spacecraft_all_modes
        from whole_spacecraft.schemas import WholeSpacecraftRunConfig

        return run_whole_spacecraft_all_modes(WholeSpacecraftRunConfig(duration_s=60.0, sample_s=10.0))
    if spec.all_modes_fn is None:
        raise AttributeError(f"{spec.module_name} has no all-modes function")
    return getattr(load_module(spec), spec.all_modes_fn)()


def supported_modes(spec: EntitySpec) -> list[str]:
    if spec.level == "whole_spacecraft":
        return ["nominal", "degradation", "fault_battery_open_circuit", "fault_rw_jamming"]
    if has_fn(spec, spec.all_modes_fn):
        results = run_all_modes(spec)
        return list(results.keys())
    if has_fn(spec, spec.nominal_fn):
        return ["nominal"]
    return []


def nominal_output_keys(spec: EntitySpec) -> list[str]:
    result = run_nominal(spec)
    if isinstance(result, dict):
        return list(result.keys())
    return [type(result).__name__]


def _flatten_numeric_scalars(value: Any, prefix: str = "") -> dict[str, float]:
    flat: dict[str, float] = {}
    if isinstance(value, bool):
        flat[prefix or "value"] = float(value)
        return flat
    if isinstance(value, (int, float)):
        flat[prefix or "value"] = float(value)
        return flat
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            flat.update(_flatten_numeric_scalars(item, child))
        return flat
    return flat


def flatten_numeric_scalars(value: Any) -> dict[str, float]:
    return _flatten_numeric_scalars(value)


def _is_numeric_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and len(value) > 0 and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in value)


def _is_numeric_vector_sequence(value: Any) -> bool:
    return (
        isinstance(value, (list, tuple))
        and len(value) > 0
        and all(isinstance(item, (list, tuple)) and len(item) > 0 and all(isinstance(part, (int, float)) and not isinstance(part, bool) for part in item) for item in value)
    )


def _vector_norm(sample: list[float] | tuple[float, ...]) -> float:
    return float(sum(float(part) ** 2 for part in sample) ** 0.5)


def extract_series(result: dict[str, Any]) -> tuple[list[float] | None, dict[str, list[float]]]:
    time_values = result.get("time_s")
    x_values = [float(x) for x in time_values] if _is_numeric_sequence(time_values) else None
    series: dict[str, list[float]] = {}
    for key, value in result.items():
        if key == "time_s":
            continue
        if _is_numeric_sequence(value) and x_values is not None and len(value) == len(x_values):
            series[key] = [float(x) for x in value]
        elif _is_numeric_vector_sequence(value) and x_values is not None and len(value) == len(x_values):
            series[f"{key}_norm"] = [_vector_norm(sample) for sample in value]
    return x_values, series


def whole_spacecraft_mode_runs() -> dict[str, dict[str, Any]]:
    from whole_spacecraft.runner import (
        run_whole_spacecraft_degradation_case,
        run_whole_spacecraft_fault_case,
        run_whole_spacecraft_native_case,
    )
    from whole_spacecraft.schemas import WholeSpacecraftRunConfig

    run_cfg = WholeSpacecraftRunConfig(duration_s=120.0, sample_s=10.0)
    nominal_summary, nominal_rows = run_whole_spacecraft_native_case(run_cfg)
    degradation_summary, degradation_rows = run_whole_spacecraft_degradation_case(run_cfg)
    fault_summary, fault_rows = run_whole_spacecraft_fault_case(
        run_cfg,
        [FaultSpec(fault_type=BatteryFaultType.OpenCircuit, onset_time_s=0.0, duration_s=-1.0, magnitude=1.0, target_id="battery")],
    )
    return {
        "nominal": {"summary": asdict(nominal_summary), "rows": [asdict(row) for row in nominal_rows]},
        "degradation": {"summary": asdict(degradation_summary), "rows": [asdict(row) for row in degradation_rows]},
        "fault_battery_open_circuit": {"summary": asdict(fault_summary), "rows": [asdict(row) for row in fault_rows]},
    }
