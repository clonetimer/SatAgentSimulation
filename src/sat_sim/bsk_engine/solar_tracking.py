"""Basilisk-scheduled *project* kinematic solar-panel tracking controller.

The commanded normal is updated before the genuine upstream SimpleSolarPanel
power calculation. This is not an upstream Basilisk hinged-body effector:
no servo torque, motor current, deployment dynamics, or spacecraft reaction
torque is represented. The official Eclipse, Sun, spacecraft, and power
messages remain the sole sources of electrical output and battery charging.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Any

import numpy as np
from Basilisk.architecture import messaging, sysModel  # type: ignore
from Basilisk.utilities import RigidBodyKinematics, macros  # type: ignore


def _unit(vec: np.ndarray) -> np.ndarray | None:
    """Return a finite 3-vector direction, never normalize an invalid input."""
    vec = np.asarray(vec, dtype=float).reshape(3)
    if not np.all(np.isfinite(vec)):
        return None
    norm = float(np.linalg.norm(vec))
    return vec / norm if norm > 1e-12 else None


def slew_towards_normal(start: Any, target: Any, max_angle_rad: float) -> np.ndarray:
    """Bounded rotation along the shortest great-circle arc on S².

    Antiparallel vectors use a reproducible perpendicular to disambiguate
    the shortest arc. No Euler-angle singularity, norm loss, or rate overshoot.
    """
    a = _unit(np.asarray(start, dtype=float))
    b = _unit(np.asarray(target, dtype=float))
    if a is None or b is None or not math.isfinite(max_angle_rad) or max_angle_rad < 0:
        raise ValueError("SOLAR_SLEW_INVALID_INPUT")
    dot = float(np.clip(np.dot(a, b), -1., 1.))
    angle = math.acos(dot)
    if angle <= max_angle_rad + 1e-12:
        return b
    if max_angle_rad == 0.:
        return a
    tangent = b - dot * a
    t_norm = float(np.linalg.norm(tangent))
    if t_norm < 1e-10:
        # Deterministically select the basis axis least aligned with a.
        axis = np.zeros(3)
        axis[int(np.argmin(np.abs(a)))] = 1.
        tangent = axis - float(np.dot(axis, a)) * a
        t_norm = float(np.linalg.norm(tangent))
    tangent = tangent / t_norm
    normal = a * math.cos(max_angle_rad) + tangent * math.sin(max_angle_rad)
    return normal / np.linalg.norm(normal)


class BasiliskScheduledSolarNormalTracker(sysModel.SysModel):
    """Move a virtual panel mounting normal, not a physical hinged wing."""

    def __init__(self, panel: Any, *, max_slew_rate_rad_s: float) -> None:
        super().__init__()
        if not math.isfinite(max_slew_rate_rad_s) or not (0. < max_slew_rate_rad_s <= 1.):
            raise ValueError("SOLAR_TRACKING_RATE_INVALID")
        self.ModelTag = "project_solar_normal_tracker"
        self.panel = panel
        self.max_slew_rate_rad_s = float(max_slew_rate_rad_s)
        self.scStateInMsg = messaging.SCStatesMsgReader()
        self.sunInMsg = messaging.SpicePlanetStateMsgReader()
        self._last_time_s: float | None = None
        self.trace: deque[dict[str, Any]] = deque(maxlen=256)

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self._last_time_s = None
        self.trace.clear()

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        now_s = float(current_sim_nanos) * macros.NANO2SEC
        dt_s = 0. if self._last_time_s is None else now_s - self._last_time_s
        self._last_time_s = now_s
        if dt_s <= 0.:
            return
        spacecraft = self.scStateInMsg()
        sun = self.sunInMsg()
        sun_direction_n = _unit(np.asarray(sun.PositionVector, dtype=float) -
                                np.asarray(spacecraft.r_BN_N, dtype=float))
        if sun_direction_n is None:
            return
        dcm_bn = np.asarray(RigidBodyKinematics.MRP2C(spacecraft.sigma_BN), dtype=float)
        desired_normal_b = _unit(dcm_bn @ sun_direction_n)
        if desired_normal_b is None:
            return
        original = _unit(np.asarray(self.panel.nHat_B, dtype=float))
        if original is None:
            raise ValueError("NATIVE_SOLAR_PANEL_NORMAL_INVALID_AT_RUNTIME")
        updated = slew_towards_normal(
            original, desired_normal_b, self.max_slew_rate_rad_s * dt_s,
        )
        # Preserve any solar-efficiency degradation already applied by the
        # higher-priority project parameter-event controller this tick.
        self.panel.setPanelParameters(
            updated.tolist(), float(self.panel.panelArea), float(self.panel.panelEfficiency),
        )
        self.trace.append({
            "time_s": now_s,
            "dt_s": dt_s,
            "normal_b": updated.tolist(),
            "sun_alignment_cos": float(np.dot(updated, desired_normal_b)),
            "moved_rad": math.acos(float(np.clip(np.dot(original, updated), -1., 1.))),
        })
