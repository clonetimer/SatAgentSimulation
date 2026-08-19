"""Small adapter-side helpers for consuming audited TaskSpec effects.

The Capability form and canonical TaskSpec are converted to runtime
``modifiers`` before adapter execution.  Adapters that still support historical
flat fields can use these helpers without re-interpreting the original request.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class RuntimeEffect:
    kind: str
    effect_id: str
    target: str
    onset_time_s: float
    duration_s: float
    magnitude: float | None
    scale: float | None
    parameters: dict[str, Any]

    @property
    def end_time_s(self) -> float | None:
        return None if self.duration_s < 0.0 else self.onset_time_s + self.duration_s

    def active_at(self, time_s: float) -> bool:
        if float(time_s) < self.onset_time_s:
            return False
        end = self.end_time_s
        return end is None or float(time_s) < end + 1e-12


def _number(value: Any, default: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _items_for_kind(spec: Mapping[str, Any], kind: str) -> Sequence[Any]:
    plural = {"fault": "faults", "degradation": "degradations", "constraint": "constraints"}[kind]
    modifiers = spec.get("modifiers") if isinstance(spec.get("modifiers"), Mapping) else {}
    items = modifiers.get(plural)
    if isinstance(items, list) and items:
        return items
    events = spec.get("events") if isinstance(spec.get("events"), Mapping) else {}
    items = events.get(plural)
    if isinstance(items, list) and items:
        return items
    legacy = spec.get(plural)
    return legacy if isinstance(legacy, list) else []


def runtime_effects(spec: Mapping[str, Any], kind: str) -> tuple[RuntimeEffect, ...]:
    """Return runtime effects while preserving registered effect identifiers."""

    if kind not in {"fault", "degradation", "constraint"}:
        raise ValueError(f"unsupported effect kind {kind!r}")
    type_key = {"fault": "fault_type", "degradation": "degradation_type", "constraint": "constraint_type"}[kind]
    out: list[RuntimeEffect] = []
    for raw in _items_for_kind(spec, kind):
        if not isinstance(raw, Mapping):
            continue
        onset = _number(raw.get("onset_time_s", raw.get("start_s", raw.get("start_time_s", 0.0))), 0.0)
        if raw.get("duration_s") is not None:
            duration = _number(raw.get("duration_s"), -1.0)
        elif raw.get("end_s") is not None:
            duration = max(0.0, _number(raw.get("end_s"), onset) - onset)
        else:
            duration = -1.0
        magnitude_raw = raw.get("magnitude", raw.get("severity"))
        scale_raw = raw.get("scale")
        effect_id = str(raw.get(type_key) or raw.get("effect") or raw.get("modifier_type") or raw.get("type") or "").strip()
        out.append(RuntimeEffect(
            kind=kind,
            effect_id=effect_id,
            target=str(raw.get("target") or raw.get("target_path") or raw.get("target_type") or "").strip(),
            onset_time_s=max(0.0, onset),
            duration_s=-1.0 if duration < 0.0 else max(0.0, duration),
            magnitude=None if magnitude_raw is None else _number(magnitude_raw, 0.0),
            scale=None if scale_raw is None else _number(scale_raw, 1.0),
            parameters=dict(raw.get("parameters") or {}) if isinstance(raw.get("parameters"), Mapping) else {},
        ))
    return tuple(out)


def effect_parameter(effect: RuntimeEffect, *names: str, default: float) -> float:
    for name in names:
        if name in effect.parameters:
            return _number(effect.parameters.get(name), default)
    if effect.scale is not None:
        return float(effect.scale)
    if effect.magnitude is not None:
        return float(effect.magnitude)
    return float(default)


__all__ = ["RuntimeEffect", "effect_parameter", "runtime_effects"]
