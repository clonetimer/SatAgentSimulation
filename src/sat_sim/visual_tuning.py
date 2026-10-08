"""Constrained Visual Composer parameter tuning helpers.

V12 deliberately reuses the platform Experiment/Run-Bundle machinery.  This
module only exposes schema-registered scalar parameters, validates a small grid
(1-3 parameters, at most 12 variants), and ranks completed runs by a declared
QoI.  It never accepts arbitrary expressions, Python code, or unregistered
TaskSpec paths.
"""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .agent_guards import evaluate_agent_guards
from .experiment_manager import expand_sweep, sweep_parameter_options
from .form_schema import capability_form_schema, task_spec_to_form_data
from .task_models import canonicalize_task_spec
from .task_validator import validate_task_spec

VISUAL_TUNING_SCHEMA_VERSION = "sat-sim.visual-tuning.v1"
MAX_TUNING_PARAMETERS = 3
MAX_VALUES_PER_PARAMETER = 5
MAX_TUNING_VARIANTS = 12


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _path_get(payload: Mapping[str, Any], path: str) -> Any:
    cursor: Any = payload
    for part in path.split("."):
        if not isinstance(cursor, Mapping) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        cooked = float(value)
        return cooked if math.isfinite(cooked) else None
    return None


def _clean_number(value: float) -> int | float:
    rounded = round(value)
    if abs(value - rounded) < 1e-12:
        return int(rounded)
    return float(f"{value:.12g}")


def _suggest_values(current: float, minimum: float | None, maximum: float | None) -> list[int | float]:
    if current == 0.0:
        if minimum is not None and maximum is not None and maximum > minimum:
            delta = (maximum - minimum) * 0.1
        elif maximum is not None and maximum > 0:
            delta = maximum * 0.1
        else:
            delta = 0.1
    else:
        delta = abs(current) * 0.15
    low = current - delta
    high = current + delta
    if minimum is not None:
        low = max(low, minimum)
        high = max(high, minimum)
    if maximum is not None:
        low = min(low, maximum)
        high = min(high, maximum)
    candidates = [_clean_number(low), _clean_number(current), _clean_number(high)]
    out: list[int | float] = []
    for value in candidates:
        if value not in out:
            out.append(value)
    return out


def _suggest_direction(metric: str) -> str:
    key = metric.lower()
    maximize_tokens = ("soc", "margin", "convergence", "availability", "efficiency", "throughput", "delivered", "success")
    minimize_tokens = ("error", "temp", "saturation", "deviation", "violation", "overshoot", "settling", "power_draw")
    if any(token in key for token in maximize_tokens):
        return "maximize"
    if any(token in key for token in minimize_tokens):
        return "minimize"
    return "minimize"


def tuning_options(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    canonical = canonicalize_task_spec(task_spec)
    capability_id = str(_mapping(canonical.get("model")).get("capability_id") or "")
    if not capability_id:
        raise ValueError("TaskSpec capability_id is required")
    schema = capability_form_schema(capability_id)
    form_data = task_spec_to_form_data(capability_id, canonical)
    fields_by_path = {
        str(item.get("path") or ""): item
        for item in schema.get("fields", [])
        if isinstance(item, Mapping) and item.get("path")
    }
    parameters: list[dict[str, Any]] = []
    for option in sweep_parameter_options(canonical):
        path = str(option.get("path") or "")
        # V12 quick tuning focuses on model parameters.  Solver/runtime fields
        # remain editable in the ordinary property panel instead of being
        # silently treated as optimization variables.
        if not path.startswith("parameters.values."):
            continue
        field = _mapping(fields_by_path.get(path))
        if str(option.get("type") or "") not in {"number", "integer"}:
            continue
        current = _finite_number(_path_get(form_data, path))
        if current is None:
            current = _finite_number(field.get("default"))
        if current is None:
            continue
        minimum = _finite_number(option.get("minimum"))
        maximum = _finite_number(option.get("maximum"))
        parameters.append(
            {
                **dict(option),
                "current": _clean_number(current),
                "suggested_values": _suggest_values(current, minimum, maximum),
                "group_id": field.get("group_id"),
                "group_title": field.get("group_title"),
            }
        )

    objective_rows = []
    seen: set[str] = set()
    outputs = _mapping(schema.get("outputs"))
    for item in outputs.get("summary_options", []) if isinstance(outputs.get("summary_options"), Sequence) else []:
        if not isinstance(item, Mapping):
            continue
        name = str(item.get("name") or "")
        if not name or name in seen or not name.startswith("qoi."):
            continue
        seen.add(name)
        objective_rows.append(
            {
                "metric": name,
                "label": str(item.get("label") or name),
                "unit": item.get("unit"),
                "description": str(item.get("description") or ""),
                "suggested_direction": _suggest_direction(name),
            }
        )
    for name in outputs.get("summary_qoi", []) if isinstance(outputs.get("summary_qoi"), Sequence) else []:
        metric = str(name)
        if not metric or metric in seen:
            continue
        seen.add(metric)
        objective_rows.append(
            {
                "metric": metric,
                "label": metric,
                "unit": None,
                "description": "当前 Capability 注册的 QoI。",
                "suggested_direction": _suggest_direction(metric),
            }
        )
    return {
        "schema_version": VISUAL_TUNING_SCHEMA_VERSION,
        "capability_id": capability_id,
        "parameter_count": len(parameters),
        "parameters": parameters,
        "objective_count": len(objective_rows),
        "objectives": objective_rows,
        "limits": {
            "max_parameters": MAX_TUNING_PARAMETERS,
            "max_values_per_parameter": MAX_VALUES_PER_PARAMETER,
            "max_variants": MAX_TUNING_VARIANTS,
        },
        "execution_backend": "existing_experiment_run_bundle",
        "arbitrary_code_accepted": False,
        "arbitrary_parameter_paths_accepted": False,
    }


def build_tuning_plan(
    task_spec: Mapping[str, Any],
    parameter_values: Mapping[str, Sequence[Any]],
    *,
    objective_metric: str,
    direction: str,
) -> dict[str, Any]:
    canonical = canonicalize_task_spec(task_spec)
    options = tuning_options(canonical)
    allowed = {str(item["path"]): item for item in options["parameters"]}
    objective_names = {str(item["metric"]) for item in options["objectives"]}
    if objective_metric not in objective_names:
        raise ValueError(f"unsupported tuning objective: {objective_metric}")
    direction = str(direction or "").lower()
    if direction not in {"minimize", "maximize"}:
        raise ValueError("direction must be minimize or maximize")
    if not (1 <= len(parameter_values) <= MAX_TUNING_PARAMETERS):
        raise ValueError(f"tuning requires 1-{MAX_TUNING_PARAMETERS} registered parameters")

    sweep: dict[str, list[int | float]] = {}
    for raw_path, raw_values in parameter_values.items():
        path = str(raw_path)
        option = allowed.get(path)
        if option is None:
            raise ValueError(f"unsupported tuning parameter path: {path}")
        if not isinstance(raw_values, Sequence) or isinstance(raw_values, (str, bytes, bytearray)):
            raise ValueError(f"tuning values for {path} must be a list")
        if not (2 <= len(raw_values) <= MAX_VALUES_PER_PARAMETER):
            raise ValueError(f"tuning values for {path} must contain 2-{MAX_VALUES_PER_PARAMETER} values")
        minimum = _finite_number(option.get("minimum"))
        maximum = _finite_number(option.get("maximum"))
        cooked: list[int | float] = []
        for raw in raw_values:
            value = _finite_number(raw)
            if value is None:
                raise ValueError(f"tuning value for {path} must be finite numeric")
            if minimum is not None and value < minimum:
                raise ValueError(f"tuning value for {path} is below minimum {minimum}")
            if maximum is not None and value > maximum:
                raise ValueError(f"tuning value for {path} is above maximum {maximum}")
            normalized = _clean_number(value)
            if normalized not in cooked:
                cooked.append(normalized)
        if len(cooked) < 2:
            raise ValueError(f"tuning values for {path} must contain at least 2 distinct values")
        sweep[path] = cooked

    variant_count = 1
    for values in sweep.values():
        variant_count *= len(values)
    if variant_count > MAX_TUNING_VARIANTS:
        raise ValueError(f"tuning expands to {variant_count} variants; limit is {MAX_TUNING_VARIANTS}")

    variants = expand_sweep(canonical, sweep, max_variants=MAX_TUNING_VARIANTS)
    invalid: list[dict[str, Any]] = []
    preview: list[dict[str, Any]] = []
    for item in variants:
        spec = item["task_spec"]
        validation = validate_task_spec(spec)
        guards = evaluate_agent_guards(spec)
        if not validation.ok or not guards.ok:
            invalid.append(
                {
                    "variant_index": item["variant_index"],
                    "parameters": item["parameters"],
                    "validation_errors": validation.to_dict().get("errors", []),
                    "guard_issues": guards.to_dict().get("issues", []),
                }
            )
        preview.append(
            {
                "variant_index": item["variant_index"],
                "parameters": item["parameters"],
                "task_id": spec.get("task", {}).get("id"),
            }
        )
    if invalid:
        raise ValueError(f"{len(invalid)} tuning variant(s) fail TaskSpec/guard validation: {invalid[:2]}")

    return {
        "schema_version": VISUAL_TUNING_SCHEMA_VERSION,
        "capability_id": options["capability_id"],
        "objective_metric": objective_metric,
        "direction": direction,
        "sweep": sweep,
        "parameter_count": len(sweep),
        "variant_count": variant_count,
        "preview": preview,
        "max_variants": MAX_TUNING_VARIANTS,
        "execution_backend": "existing_experiment_run_bundle",
    }


def rank_tuning_rows(rows: Sequence[Mapping[str, Any]], *, objective_metric: str, direction: str) -> list[dict[str, Any]]:
    direction = str(direction or "").lower()
    if direction not in {"minimize", "maximize"}:
        raise ValueError("direction must be minimize or maximize")
    ranked: list[dict[str, Any]] = []
    for row in rows:
        value = _finite_number(row.get("objective_value"))
        if value is None:
            continue
        ranked.append({**dict(row), "objective_value": value})
    ranked.sort(key=lambda item: item["objective_value"], reverse=(direction == "maximize"))
    for index, item in enumerate(ranked, start=1):
        item["rank"] = index
        item["is_best"] = index == 1
    return ranked


__all__ = [
    "VISUAL_TUNING_SCHEMA_VERSION",
    "MAX_TUNING_PARAMETERS",
    "MAX_VALUES_PER_PARAMETER",
    "MAX_TUNING_VARIANTS",
    "build_tuning_plan",
    "rank_tuning_rows",
    "tuning_options",
]
