"""Schema-backed dispersion parameter registry.

This module deliberately keeps the public experiment API TaskSpec-centric.  It
borrows the terminology of Basilisk's MonteCarloController (dispersion,
sampling plan, seed, retained runs), but it does not require callers to use the
Basilisk controller object directly.  The adapter layer can later translate
these records into Basilisk dispersion objects for fully-native BSKSim cases.
"""
from __future__ import annotations

from typing import Any, Mapping

from sat_sim.experiment_manager import sweep_parameter_options

_NUMERIC = {"number", "integer"}


def default_distribution_for_option(option: Mapping[str, Any]) -> dict[str, Any]:
    """Return a conservative default dispersion for one schema-backed field."""
    field_type = str(option.get("type") or "")
    current = option.get("current")
    if field_type in _NUMERIC:
        center = float(current if isinstance(current, (int, float)) else 0.0)
        spread = max(abs(center) * 0.05, 1.0 if center == 0.0 else abs(center) * 0.05)
        return {"distribution": "normal", "mean": center, "std": spread}
    if field_type == "boolean":
        return {"distribution": "choice", "values": [False, True]}
    enum = option.get("enum") or []
    if enum:
        return {"distribution": "choice", "values": list(enum)}
    return {"distribution": "choice", "values": [current] if current is not None else [""]}


def dispersion_parameter_options(base_task_spec: Mapping[str, Any]) -> list[dict[str, Any]]:
    """List fields that may be used in sweep or Monte Carlo studies.

    The output extends the existing sweep parameter options with recommended
    distributions.  Complex arrays/objects are intentionally excluded until a
    dedicated vector-dispersion editor is available.
    """
    options: list[dict[str, Any]] = []
    for option in sweep_parameter_options(base_task_spec):
        enriched = dict(option)
        enriched["current"] = None
        enriched["supported_distributions"] = ["choice"]
        if str(option.get("type") or "") in _NUMERIC:
            enriched["supported_distributions"] = ["normal", "uniform", "triangular", "choice"]
        elif option.get("enum"):
            enriched["supported_distributions"] = ["choice"]
        enriched["default_distribution"] = default_distribution_for_option(enriched)
        options.append(enriched)
    return options
