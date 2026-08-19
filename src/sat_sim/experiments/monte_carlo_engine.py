"""Monte Carlo sampling utilities for TaskSpec experiments."""
from __future__ import annotations

import copy
import random
from typing import Any, Mapping, Sequence

from sat_sim.experiment_manager import EXPERIMENT_SCHEMA_VERSION, _set_path, validate_sweep_paths
from sat_sim.task_models import canonicalize_task_spec
from .statistics import summarize_numeric_samples


def _as_float(value: Any, *, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _sample_one(rng: random.Random, spec: Mapping[str, Any]) -> Any:
    distribution = str(spec.get("distribution") or spec.get("type") or "normal").lower()
    if distribution == "normal":
        return rng.gauss(_as_float(spec.get("mean")), max(_as_float(spec.get("std"), default=1.0), 0.0))
    if distribution == "uniform":
        low = _as_float(spec.get("min", spec.get("low", 0.0)))
        high = _as_float(spec.get("max", spec.get("high", low)))
        if high < low:
            low, high = high, low
        return rng.uniform(low, high)
    if distribution == "triangular":
        low = _as_float(spec.get("min", spec.get("low", 0.0)))
        high = _as_float(spec.get("max", spec.get("high", low)))
        mode = _as_float(spec.get("mode", (low + high) / 2.0))
        if high < low:
            low, high = high, low
        mode = min(max(mode, low), high)
        return rng.triangular(low, high, mode)
    if distribution == "choice":
        values = spec.get("values") or spec.get("choices") or []
        if not isinstance(values, Sequence) or isinstance(values, (str, bytes)) or not values:
            raise ValueError("choice distribution requires a non-empty values list")
        return copy.deepcopy(list(values)[rng.randrange(len(values))])
    raise ValueError(f"unsupported Monte Carlo distribution: {distribution}")


def expand_monte_carlo(
    base_task_spec: Mapping[str, Any],
    dispersions: Mapping[str, Mapping[str, Any]],
    *,
    sample_count: int = 16,
    seed: int = 1,
    max_variants: int = 256,
) -> list[dict[str, Any]]:
    """Expand a base TaskSpec into deterministic Monte Carlo samples.

    Parameters are validated against the same schema-backed path registry as
    enumerated sweeps.  This keeps the feature compatible with the workbench and
    prevents users or models from sampling unsupported fields.
    """
    canonical = canonicalize_task_spec(base_task_spec)
    if not dispersions:
        raise ValueError("Monte Carlo experiment requires at least one dispersion")
    if sample_count < 1:
        raise ValueError("sample_count must be greater than zero")
    if sample_count > max_variants:
        raise ValueError(f"Monte Carlo experiment expands to {sample_count} samples; limit is {max_variants}")
    validate_sweep_paths(canonical, {path: [0] for path in dispersions})
    rng = random.Random(int(seed))
    seed_rng = random.Random(int(seed) ^ 0x5DEECE66D)
    paths = list(dispersions)
    variants: list[dict[str, Any]] = []
    sampled_values: dict[str, list[Any]] = {path: [] for path in paths}
    for index in range(int(sample_count)):
        spec = copy.deepcopy(canonical)
        parameters: dict[str, Any] = {}
        for path in paths:
            value = _sample_one(rng, dispersions[path])
            parameters[path] = value
            sampled_values[path].append(value)
            _set_path(spec, path, value)
            if path.startswith("parameters.values."):
                _set_path(spec, "model.config." + path.removeprefix("parameters.values."), value)
            elif path.startswith("model.config."):
                _set_path(spec, "parameters.values." + path.removeprefix("model.config."), value)
        simulation_seed = seed_rng.randrange(0, 2**32)
        spec["simulation"]["random_seed"] = simulation_seed
        solver = spec["simulation"].get("solver")
        if isinstance(solver, dict):
            solver["deterministic_seed"] = simulation_seed
        spec["task"]["id"] = f"{canonical['task']['id']}_mc{index + 1:03d}"
        spec["task"]["name"] = f"{canonical['task']['name']} · Monte Carlo 样本 {index + 1}"
        spec.setdefault("metadata", {})["experiment_variant"] = {
            "schema_version": EXPERIMENT_SCHEMA_VERSION,
            "experiment_type": "monte_carlo",
            "variant_index": index,
            "seed": int(seed),
            "parameter_seed": int(seed),
            "simulation_seed": simulation_seed,
            "sample_index": index,
            "parameters": copy.deepcopy(parameters),
            "dispersions": copy.deepcopy(dict(dispersions)),
        }
        variants.append({"variant_index": index, "parameters": parameters, "simulation_seed": simulation_seed, "task_spec": canonicalize_task_spec(spec)})
    for variant in variants:
        variant.setdefault("sampling_statistics", {path: summarize_numeric_samples(values) for path, values in sampled_values.items()})
    return variants
