"""ADCS operational constraints.

This module keeps normal control/actuator limits separate from hardware faults
and performance degradations.  The first registered constraint is the reaction-
wheel speed limit; reaching it does not change the wheel health state.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any

from components.reaction_wheel.constraints import (
    RWConstraintType,
    ReactionWheelConstraintSpec,
    apply_runtime_reaction_wheel_constraint,
    build_reaction_wheel_constraint_spec,
)


class ADCSConstraintType(str, Enum):
    RW_SPEED_LIMIT = "adcs_reaction_wheel_speed_limit"


@dataclass(frozen=True)
class AdcsConstraintSpec:
    constraint_type: ADCSConstraintType | str
    onset_time_s: float = 0.0
    duration_s: float = -1.0
    magnitude: float = 1.0
    target_id: str | None = "rw_0"


def build_adcs_constraint_spec(
    constraint_type: ADCSConstraintType | str = ADCSConstraintType.RW_SPEED_LIMIT,
    *,
    onset_time_s: float = 0.0,
    duration_s: float = -1.0,
    magnitude: float = 1.0,
    target_id: str = "rw_0",
) -> AdcsConstraintSpec:
    return AdcsConstraintSpec(
        constraint_type=constraint_type,
        onset_time_s=float(onset_time_s),
        duration_s=float(duration_s),
        magnitude=max(0.0, min(1.0, float(magnitude))),
        target_id=target_id,
    )


def to_reaction_wheel_constraint(spec: AdcsConstraintSpec | Any) -> ReactionWheelConstraintSpec:
    return build_reaction_wheel_constraint_spec(
        RWConstraintType.SPEED_LIMIT,
        onset_time_s=float(getattr(spec, "onset_time_s", 0.0)),
        duration_s=float(getattr(spec, "duration_s", -1.0)),
        magnitude=float(getattr(spec, "magnitude", 1.0)),
        target_id=str(getattr(spec, "target_id", "rw_0") or "rw_0"),
    )


def apply_runtime_adcs_constraint(
    spec: AdcsConstraintSpec | Any,
    component_registry: dict[str, Any],
    *,
    set_attr=None,
    resolve_component=None,
) -> tuple[dict[str, object], ...]:
    target_id = str(getattr(spec, "target_id", "") or "")
    rw_target = (
        component_registry.get(target_id)
        or component_registry.get("rw_cluster")
        or component_registry.get("rw_0")
    )
    if rw_target is None and resolve_component is not None:
        rw_target = resolve_component(target_id, "reaction_wheel")
    if rw_target is None:
        return ()
    return apply_runtime_reaction_wheel_constraint(
        rw_target,
        to_reaction_wheel_constraint(spec),
        set_attr=set_attr,
    )


__all__ = [
    "ADCSConstraintType",
    "AdcsConstraintSpec",
    "build_adcs_constraint_spec",
    "to_reaction_wheel_constraint",
    "apply_runtime_adcs_constraint",
]
