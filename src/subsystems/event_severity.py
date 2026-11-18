"""Shared severity semantics for native subsystem runtime events.

Continuous parameter effects scale from their nominal state toward the declared
fault/degradation state. Binary overrides and absolute clamps remain discrete;
they are not fractionalized by severity.
"""
from __future__ import annotations

from dataclasses import replace
from math import isfinite
from typing import Any, Mapping, Sequence


def event_severity(event: Mapping[str, Any]) -> float:
    """Read a normalized event severity from TaskSpec fields.

    severity is preferred. The generic UI's normalized magnitude is a
    compatibility fallback. Missing values preserve the component default (1).
    """
    parameters = event.get("parameters")
    parameter_map = parameters if isinstance(parameters, Mapping) else {}
    raw = event.get(
        "severity",
        parameter_map.get("severity", event.get("magnitude", parameter_map.get("magnitude", 1.0))),
    )
    try:
        value = float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError("event severity must be a finite number in [0, 1]") from exc
    if not isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("event severity must be a finite number in [0, 1]")
    return value


def scale_parameter_effects(effects: Sequence[Any], severity: float) -> tuple[Any, ...]:
    """Scale continuous ParameterEffects while preserving discrete operations.

    For multiply, severity interpolates from the nominal multiplier 1.0
    toward the declared multiplier. Additive and fractional degradation/growth
    operations scale linearly. override and clamps are discrete/absolute
    operations and retain their declared target.
    """
    severity = float(severity)
    if not isfinite(severity) or not 0.0 <= severity <= 1.0:
        raise ValueError("event severity must be a finite number in [0, 1]")
    scaled: list[Any] = []
    for effect in effects:
        operation = str(getattr(effect, "operation", ""))
        try:
            magnitude = float(getattr(effect, "magnitude"))
        except (TypeError, ValueError, AttributeError):
            scaled.append(effect)
            continue
        if operation == "multiply":
            adjusted = 1.0 + (magnitude - 1.0) * severity
        elif operation in {"add", "add_loss", "degrade_multiply", "grow_multiply", "grow_add"}:
            adjusted = magnitude * severity
        else:
            adjusted = magnitude
        if adjusted == magnitude:
            scaled.append(effect)
        else:
            scaled.append(replace(effect, magnitude=adjusted))
    return tuple(scaled)


__all__ = ["event_severity", "scale_parameter_effects"]
