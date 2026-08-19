"""Reaction-wheel operational constraints and state events.

Operational constraints represent normal physical or control-system limits, not
hardware failures.  Reaching a wheel-speed limit therefore belongs here rather
than in :mod:`components.reaction_wheel.faults`.
"""
from __future__ import annotations

from dataclasses import dataclass, fields, is_dataclass, replace
from enum import Enum
from typing import Any, Sequence


class RWConstraintType(str, Enum):
    """Supported reaction-wheel operational constraints."""

    SPEED_LIMIT = "reaction_wheel_speed_limit"


@dataclass(frozen=True)
class ReactionWheelConstraintSpec:
    """Time-window operational constraint for a reaction wheel."""

    constraint_type: RWConstraintType | str
    onset_time_s: float = 0.0
    duration_s: float = -1.0
    magnitude: float = 1.0
    target_id: str | None = "rw_0"

    def active_at(self, time_s: float) -> bool:
        if time_s < self.onset_time_s:
            return False
        return self.duration_s < 0.0 or time_s < self.onset_time_s + self.duration_s


def build_reaction_wheel_constraint_spec(
    constraint_type: RWConstraintType | str = RWConstraintType.SPEED_LIMIT,
    *,
    onset_time_s: float = 0.0,
    duration_s: float = -1.0,
    magnitude: float = 1.0,
    target_id: str = "rw_0",
) -> ReactionWheelConstraintSpec:
    return ReactionWheelConstraintSpec(
        constraint_type=constraint_type,
        onset_time_s=float(onset_time_s),
        duration_s=float(duration_s),
        magnitude=max(0.0, min(1.0, float(magnitude))),
        target_id=target_id,
    )


def _constraint_key(spec: Any) -> str:
    raw = getattr(spec, "constraint_type", getattr(spec, "fault_type", spec))
    return str(getattr(raw, "value", raw))


def _target_wheel_index(target_id: str | None, wheel_count: int) -> int | None:
    text = str(target_id or "").lower()
    if not text or text.endswith(".all") or text.endswith("_all"):
        return None
    import re

    match = re.search(r"(?:rw|wheel|reaction_wheel)[._-]?(\d+)$", text)
    if match:
        index = int(match.group(1))
        return index if 0 <= index < wheel_count else None
    if text.endswith(".primary"):
        return 0 if wheel_count else None
    return 0 if wheel_count else None


def _scale_limit(value: float, magnitude: float) -> float:
    return float(value) * max(0.05, 1.0 - 0.95 * max(0.0, min(1.0, magnitude)))


def apply_reaction_wheel_spec_constraints(
    wheel_specs: Sequence[Any],
    constraint_specs: Sequence[Any],
) -> tuple[Any, ...]:
    """Apply operational limits to immutable reaction-wheel specifications."""

    specs = list(wheel_specs)
    for constraint in constraint_specs:
        if _constraint_key(constraint) not in {RWConstraintType.SPEED_LIMIT.value, "reaction_wheel_speed_limit"}:
            continue
        target_index = _target_wheel_index(getattr(constraint, "target_id", None), len(specs))
        indices = range(len(specs)) if target_index is None else (target_index,)
        magnitude = max(0.0, min(1.0, float(getattr(constraint, "magnitude", 1.0))))
        for idx in indices:
            spec = specs[idx]
            if hasattr(spec, "omega_max_rad_s"):
                specs[idx] = replace(spec, omega_max_rad_s=_scale_limit(spec.omega_max_rad_s, magnitude))
    return tuple(specs)


def _rw_config_entries(rw_target: Any) -> list[object]:
    effector = getattr(rw_target, "effector", rw_target)
    data = getattr(effector, "ReactionWheelData", None)
    if data is None:
        return []
    try:
        return [data[idx] for idx in range(len(data))]
    except Exception:
        return []


def apply_runtime_reaction_wheel_constraint(
    rw_target: Any,
    spec: Any,
    *,
    set_attr=None,
) -> tuple[dict[str, object], ...]:
    """Apply a runtime wheel constraint without changing health classification."""

    if _constraint_key(spec) not in {RWConstraintType.SPEED_LIMIT.value, "reaction_wheel_speed_limit"}:
        return ()
    entries = _rw_config_entries(rw_target)
    if not entries:
        return ()
    target_index = _target_wheel_index(getattr(spec, "target_id", None), len(entries))
    indices = range(len(entries)) if target_index is None else (target_index,)
    magnitude = max(0.0, min(1.0, float(getattr(spec, "magnitude", 1.0))))
    setter = set_attr or (lambda obj, attr, value: setattr(obj, attr, value))
    mutations: list[dict[str, object]] = []
    for idx in indices:
        cfg = entries[idx]
        if not hasattr(cfg, "Omega_max"):
            continue
        before = getattr(cfg, "Omega_max")
        after = _scale_limit(before, magnitude)
        setter(cfg, "Omega_max", after)
        mutations.append({"wheel_index": idx, "attr": "Omega_max", "before": before, "after": after})
    return tuple(mutations)


def apply_reaction_wheel_constraints(config: Any, constraint_specs: Sequence[Any]) -> Any:
    """Apply operational limits to local dataclass configuration objects."""

    cfg = config
    for spec in constraint_specs:
        if _constraint_key(spec) not in {RWConstraintType.SPEED_LIMIT.value, "reaction_wheel_speed_limit"}:
            continue
        if not hasattr(cfg, "max_speed_rad_s"):
            continue
        magnitude = max(0.0, min(1.0, float(getattr(spec, "magnitude", 1.0))))
        value = cfg.max_speed_rad_s
        if isinstance(value, tuple):
            new_value = tuple(_scale_limit(v, magnitude) for v in value)
        elif isinstance(value, list):
            new_value = [_scale_limit(v, magnitude) for v in value]
        else:
            new_value = _scale_limit(value, magnitude)
        if is_dataclass(cfg) and "max_speed_rad_s" in {f.name for f in fields(cfg)}:
            cfg = replace(cfg, max_speed_rad_s=new_value)
    return cfg


__all__ = [
    "RWConstraintType",
    "ReactionWheelConstraintSpec",
    "build_reaction_wheel_constraint_spec",
    "apply_reaction_wheel_spec_constraints",
    "apply_runtime_reaction_wheel_constraint",
    "apply_reaction_wheel_constraints",
]
