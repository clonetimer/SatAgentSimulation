"""THERM-1C thermal control policies for reduced-order thermal networks.

The policies in this module are intentionally simple and auditable.  They model
heater command generation for early spacecraft thermal trade studies; they are
not a flight software controller, not an HIL controller, and not flight tuned.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping
import math

THERMAL_CONTROL_SCHEMA_VERSION = "therm1c.thermal_control_policy.v1"
THERMAL_CONTROL_MODES = ("threshold", "hysteresis", "pid")


class ThermalControlError(ValueError):
    """Raised when a thermal-control policy is invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise ThermalControlError(f"{name} must be a finite number")
    return float(value)


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise ThermalControlError(f"{name} must be non-negative")
    return out


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise ThermalControlError(f"{name} must be positive")
    return out


def _clip(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


@dataclass(frozen=True)
class ThermalControlPolicy:
    """Heater control policy used by the reduced-order thermal network.

    ``threshold`` preserves the legacy THERM-1/THERM-1B behavior.
    ``hysteresis`` implements a stateful thermostat with on/off thresholds.
    ``pid`` implements a bounded heater-power PID command for precision-control
    studies.  PID gains are public-reference/user-supplied assumptions and do not
    imply flight tuning.
    """

    mode: str = "threshold"
    node_id: str = "internal"
    max_power_w: float = 8.0
    setpoint_c: float = 2.0
    deadband_c: float = 2.0
    on_below_c: float | None = None
    off_above_c: float | None = None
    initial_on: bool = False
    kp_w_per_c: float = 0.0
    ki_w_per_c_s: float = 0.0
    kd_w_s_per_c: float = 0.0
    integral_limit_c_s: float = 1.0e5
    derivative_filter_s: float = 0.0
    min_power_w: float = 0.0

    @classmethod
    def from_legacy(cls, *, node_id: str, power_w: float, setpoint_c: float, deadband_c: float) -> "ThermalControlPolicy":
        return cls(
            mode="threshold",
            node_id=str(node_id),
            max_power_w=_nonnegative(power_w, "parameters.heater_power_w"),
            setpoint_c=_finite(setpoint_c, "parameters.heater_setpoint_c"),
            deadband_c=_nonnegative(deadband_c, "parameters.heater_deadband_c"),
        )

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, legacy: "ThermalControlPolicy") -> "ThermalControlPolicy":
        """Build a policy from ``parameters.heater_control`` with legacy defaults."""

        mode = str(data.get("mode", legacy.mode)).strip().lower()
        if mode not in THERMAL_CONTROL_MODES:
            raise ThermalControlError(f"heater_control.mode must be one of {', '.join(THERMAL_CONTROL_MODES)}")
        setpoint = _finite(data.get("setpoint_c", legacy.setpoint_c), "heater_control.setpoint_c")
        deadband = _nonnegative(data.get("deadband_c", legacy.deadband_c), "heater_control.deadband_c")
        max_power = _nonnegative(data.get("max_power_w", data.get("power_w", legacy.max_power_w)), "heater_control.max_power_w")
        min_power = _nonnegative(data.get("min_power_w", 0.0), "heater_control.min_power_w")
        if min_power > max_power:
            raise ThermalControlError("heater_control.min_power_w cannot exceed max_power_w")
        on_below = data.get("on_below_c")
        off_above = data.get("off_above_c")
        on_below_value = None if on_below is None else _finite(on_below, "heater_control.on_below_c")
        off_above_value = None if off_above is None else _finite(off_above, "heater_control.off_above_c")
        if mode == "hysteresis":
            on_threshold = on_below_value if on_below_value is not None else setpoint - deadband
            off_threshold = off_above_value if off_above_value is not None else setpoint + deadband
            if on_threshold >= off_threshold:
                raise ThermalControlError("heater_control hysteresis on_below_c must be lower than off_above_c")
            on_below_value = on_threshold
            off_above_value = off_threshold
        kp = _nonnegative(data.get("kp_w_per_c", data.get("kp", 0.0)), "heater_control.kp_w_per_c")
        ki = _nonnegative(data.get("ki_w_per_c_s", data.get("ki", 0.0)), "heater_control.ki_w_per_c_s")
        kd = _nonnegative(data.get("kd_w_s_per_c", data.get("kd", 0.0)), "heater_control.kd_w_s_per_c")
        if mode == "pid" and max_power > 0.0 and kp == 0.0 and ki == 0.0 and kd == 0.0:
            # A conservative deterministic default: proportional-only command.
            kp = max_power / max(1.0, max(deadband, 1.0))
        return cls(
            mode=mode,
            node_id=str(data.get("node", data.get("node_id", legacy.node_id))),
            max_power_w=max_power,
            setpoint_c=setpoint,
            deadband_c=deadband,
            on_below_c=on_below_value,
            off_above_c=off_above_value,
            initial_on=bool(data.get("initial_on", legacy.initial_on)),
            kp_w_per_c=kp,
            ki_w_per_c_s=ki,
            kd_w_s_per_c=kd,
            integral_limit_c_s=_positive(data.get("integral_limit_c_s", 1.0e5), "heater_control.integral_limit_c_s"),
            derivative_filter_s=_nonnegative(data.get("derivative_filter_s", 0.0), "heater_control.derivative_filter_s"),
            min_power_w=min_power,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": THERMAL_CONTROL_SCHEMA_VERSION,
            "mode": self.mode,
            "node_id": self.node_id,
            "max_power_w": self.max_power_w,
            "setpoint_c": self.setpoint_c,
            "deadband_c": self.deadband_c,
            "on_below_c": self.on_below_c,
            "off_above_c": self.off_above_c,
            "initial_on": self.initial_on,
            "kp_w_per_c": self.kp_w_per_c,
            "ki_w_per_c_s": self.ki_w_per_c_s,
            "kd_w_s_per_c": self.kd_w_s_per_c,
            "integral_limit_c_s": self.integral_limit_c_s,
            "derivative_filter_s": self.derivative_filter_s,
            "min_power_w": self.min_power_w,
        }


@dataclass(frozen=True)
class ThermalControlState:
    """Runtime state for stateful heater controllers."""

    on: bool = False
    integral_error_c_s: float = 0.0
    previous_error_c: float | None = None
    filtered_derivative_c_s: float = 0.0


@dataclass(frozen=True)
class ThermalControlCommand:
    """One heater command sample and the controller state after this sample."""

    mode: str
    node_id: str
    temperature_c: float
    setpoint_c: float
    error_c: float
    heater_on: bool
    power_w: float
    saturation: bool
    state: ThermalControlState
    threshold_on_c: float | None = None
    threshold_off_c: float | None = None
    proportional_w: float = 0.0
    integral_w: float = 0.0
    derivative_w: float = 0.0

    def trace_fields(self) -> dict[str, Any]:
        return {
            "thermal.control.mode": self.mode,
            "thermal.control.node_id": self.node_id,
            "thermal.control.setpoint_c": self.setpoint_c,
            "thermal.control.error_c": self.error_c,
            "thermal.control.heater_on": self.heater_on,
            "thermal.control.power_w": self.power_w,
            "thermal.control.saturation": self.saturation,
            "thermal.control.integral_error_c_s": self.state.integral_error_c_s,
            "thermal.control.derivative_c_per_s": self.state.filtered_derivative_c_s,
            "thermal.control.threshold_on_c": self.threshold_on_c,
            "thermal.control.threshold_off_c": self.threshold_off_c,
            "thermal.control.pid.p_w": self.proportional_w,
            "thermal.control.pid.i_w": self.integral_w,
            "thermal.control.pid.d_w": self.derivative_w,
        }


def initial_control_state(policy: ThermalControlPolicy) -> ThermalControlState:
    return ThermalControlState(on=policy.initial_on)


def compute_heater_command(
    policy: ThermalControlPolicy,
    state: ThermalControlState,
    *,
    temperature_c: float,
    dt_s: float,
) -> ThermalControlCommand:
    """Compute one heater command sample for the selected policy."""

    temp = _finite(temperature_c, "temperature_c")
    dt = max(1.0e-12, _positive(dt_s, "dt_s"))
    error = policy.setpoint_c - temp
    threshold_on = policy.setpoint_c - policy.deadband_c
    threshold_off: float | None = None
    p_term = i_term = d_term = 0.0
    saturation = False

    if policy.mode == "threshold":
        on = temp <= threshold_on
        power = policy.max_power_w if on else 0.0
        next_state = ThermalControlState(on=on, integral_error_c_s=state.integral_error_c_s, previous_error_c=error, filtered_derivative_c_s=state.filtered_derivative_c_s)
    elif policy.mode == "hysteresis":
        threshold_on = policy.on_below_c if policy.on_below_c is not None else policy.setpoint_c - policy.deadband_c
        threshold_off = policy.off_above_c if policy.off_above_c is not None else policy.setpoint_c + policy.deadband_c
        if temp <= threshold_on:
            on = True
        elif temp >= threshold_off:
            on = False
        else:
            on = state.on
        power = policy.max_power_w if on else 0.0
        next_state = ThermalControlState(on=on, integral_error_c_s=state.integral_error_c_s, previous_error_c=error, filtered_derivative_c_s=state.filtered_derivative_c_s)
    else:  # pid
        integral = _clip(state.integral_error_c_s + error * dt, -policy.integral_limit_c_s, policy.integral_limit_c_s)
        previous_error = error if state.previous_error_c is None else state.previous_error_c
        derivative = (error - previous_error) / dt
        if policy.derivative_filter_s > 0.0:
            alpha = dt / (policy.derivative_filter_s + dt)
            derivative = alpha * derivative + (1.0 - alpha) * state.filtered_derivative_c_s
        p_term = policy.kp_w_per_c * error
        i_term = policy.ki_w_per_c_s * integral
        d_term = policy.kd_w_s_per_c * derivative
        raw_power = p_term + i_term + d_term
        power = _clip(raw_power, 0.0, policy.max_power_w)
        if 0.0 < power < policy.min_power_w:
            power = policy.min_power_w
        saturation = raw_power != power or power >= policy.max_power_w - 1.0e-12
        on = power > 0.0
        next_state = ThermalControlState(on=on, integral_error_c_s=integral, previous_error_c=error, filtered_derivative_c_s=derivative)

    return ThermalControlCommand(
        mode=policy.mode,
        node_id=policy.node_id,
        temperature_c=temp,
        setpoint_c=policy.setpoint_c,
        error_c=error,
        heater_on=on,
        power_w=power,
        saturation=saturation,
        state=next_state,
        threshold_on_c=threshold_on,
        threshold_off_c=threshold_off,
        proportional_w=p_term,
        integral_w=i_term,
        derivative_w=d_term,
    )


__all__ = [
    "THERMAL_CONTROL_SCHEMA_VERSION",
    "THERMAL_CONTROL_MODES",
    "ThermalControlError",
    "ThermalControlPolicy",
    "ThermalControlState",
    "ThermalControlCommand",
    "initial_control_state",
    "compute_heater_command",
]
