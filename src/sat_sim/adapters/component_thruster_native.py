"""Finite nominal single-thruster component on a free Basilisk test carrier."""
from __future__ import annotations

import math
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from components.thruster.model import G0
from sat_sim.adapter_base import SimulationResult
from sat_sim.task_validator import ValidationIssue

from .component_source_native_misc import _row_base


_NS_PER_S = 1_000_000_000
_MAX_STEPS = 100_000
_INITIAL_FUEL_KG = 1.0
_TANK_CAPACITY_KG = 2.0
_HUB_MASS_KG = 10.0
_PREFIX = "propulsion.thruster."
_CAPABILITY = "component.thruster.v1"


class _InputError(ValueError):
    def __init__(self, path: str, message: str):
        super().__init__(message)
        self.path = path


@dataclass(frozen=True)
class _NativeCase:
    duration_ns: int
    sample_ns: int
    step_ns: int
    commands_ns: tuple[int, ...]
    start_ns: int
    burn_ns: int
    thrust_n: float
    isp_s: float

    @property
    def step_fuel_kg(self) -> float:
        return self.thrust_n / (self.isp_s * G0) * (self.step_ns / _NS_PER_S)

    @property
    def rounding_budget_kg(self) -> float:
        # Two accumulations (tank mass and force integral), allowing one
        # fixture-mass ULP each per firing step and the two switching edges.
        return 2 * (self.burn_ns // self.step_ns + 2) * math.ulp(_INITIAL_FUEL_KG)


def _number(value: Any, path: str, minimum: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _InputError(path, "requires a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise _InputError(path, "number exceeds the supported range") from exc
    if not math.isfinite(number) or number < minimum:
        raise _InputError(path, f"requires a finite number >= {minimum}")
    return number


def _nanoseconds(seconds: float, path: str) -> int:
    # Match Basilisk macros.sec2nano. The builder accepts seconds as doubles.
    if seconds * _NS_PER_S >= 2**52:
        raise _InputError(path, "time exceeds the supported nanosecond precision")
    return int(seconds * _NS_PER_S + 0.5)


def _require_seconds_round_trip(ns: int, path: str) -> None:
    # The shared builder and command message accept seconds as doubles.
    if int((ns / _NS_PER_S) * _NS_PER_S + 0.5) != ns:
        raise _InputError(path, "native scheduling time must round-trip exactly through seconds")


def compile_native_thruster(spec: Mapping[str, Any]) -> _NativeCase:
    target = spec.get("target", {})
    if not isinstance(target, Mapping) or target.get("mode", "nominal") != "nominal":
        raise _InputError("$.target.mode", "native thruster supports nominal mode only")
    for location, container in (("$", spec), ("$.modifiers", spec.get("modifiers", {}))):
        if not isinstance(container, Mapping):
            raise _InputError(location, "native thruster requires a mapping")
        for key in ("faults", "degradations", "constraints"):
            if container.get(key):
                raise _InputError(f"{location}.{key}", "native thruster supports no events or constraints")

    p = spec["parameters"]
    sim = spec.get("simulation", {})
    if not isinstance(sim, Mapping):
        raise _InputError("$.simulation", "native thruster requires a simulation mapping")
    sample_s = _number(sim.get("sample_s"), "$.simulation.sample_s")
    sample = _nanoseconds(sample_s, "$.simulation.sample_s")
    duration_path = "$.simulation.duration_s"
    duration = _nanoseconds(_number(sim.get("duration_s"), duration_path), duration_path)
    step_path = "$.parameters.native_step_s"
    step = _nanoseconds(_number(p.get("native_step_s", 0.01), step_path, 1e-9), step_path)
    _require_seconds_round_trip(step, step_path)
    solver = sim.get("solver")
    if solver is not None and not isinstance(solver, Mapping):
        raise _InputError("$.simulation.solver", "native thruster requires a solver mapping")
    for location, source in (("$.simulation", sim), ("$.simulation.solver", solver or {})):
        if "step_s" in source:
            path = f"{location}.step_s"
            if _nanoseconds(_number(source["step_s"], path), path) != step:
                raise _InputError(path, "integration step must match native_step_s after nanosecond rounding")
    if not duration or not sample or duration % sample or sample % step:
        raise _InputError("$.simulation", "duration, sample and native step require complete positive intervals")
    if duration // step > _MAX_STEPS:
        raise _InputError("$.simulation", f"native thruster exceeds {_MAX_STEPS} integration steps")

    thrust = _number(p.get("thrust_n", 1.0), "$.parameters.thrust_n")
    isp = _number(p.get("isp_s", 280.0), "$.parameters.isp_s", 1e-6)
    n_periods = duration // sample

    def command(value: Any, path: str) -> int:
        seconds = _number(value, path)
        if seconds > sample_s:
            raise _InputError(path, "on-time cannot exceed the command period")
        ns = _nanoseconds(seconds, path)
        if (seconds > 0 and ns == 0) or ns % step:
            raise _InputError(path, "nonzero on-time must span complete native steps")
        return ns

    scalar_path = "$.parameters.on_time_s"
    if "on_time_profile_s" in p:
        if "on_time_s" in p:
            command(p["on_time_s"], scalar_path)
        profile_path = "$.parameters.on_time_profile_s"
        raw = p["on_time_profile_s"]
        if not isinstance(raw, list) or not 1 <= len(raw) <= n_periods:
            raise _InputError(profile_path, "requires a nonempty list of at most one value per period")
        commands = [command(value, f"{profile_path}[{i}]") for i, value in enumerate(raw)]
    else:
        commands = [command(p.get("on_time_s", 0.1), scalar_path)]
    commands.extend([commands[-1]] * (n_periods - len(commands)))

    start = 0
    burn = 0
    for index, value in enumerate(commands):
        if value:
            time = index * sample
            if burn and time != start + burn:
                raise _InputError("$.parameters", "native thruster requires one contiguous burn or all-zero commands")
            if not burn:
                start = time
            burn += value

    _require_seconds_round_trip(burn, "$.parameters")

    # The native force magnitude and exhaust velocity require representable
    # squared force and Isp*g0, even when a ratio of those inputs is finite.
    if thrust > math.sqrt(sys.float_info.max) or not math.isfinite(isp * G0):
        raise _InputError("$.parameters", "thrust or Isp exceeds the native numerical range")
    # A Runge-Kutta stage at a switching edge can briefly draw more fuel
    # than the final recorded mass difference shows. Reserve one native step.
    case = _NativeCase(duration, sample, step, tuple(commands), start, burn, thrust, isp)
    if burn and thrust > 0.0:
        if case.step_fuel_kg * (burn // step + 1) >= _INITIAL_FUEL_KG:
            raise _InputError("$.parameters", "burn plus one native step of fuel reserve would exhaust the fixture")
        if case.step_fuel_kg < case.rounding_budget_kg:
            raise _InputError("$.parameters", "native tank resolution cannot bound accumulated rounding to one fuel step")
    return case


def validate_native_thruster(spec: Mapping[str, Any]) -> tuple[ValidationIssue, ...]:
    try:
        compile_native_thruster(spec)
        from subsystems.propulsion.builder import _require_propulsion_basilisk

        _require_propulsion_basilisk()
    except _InputError as exc:
        return (ValidationIssue("error", exc.path, str(exc), "native_thruster"),)
    except (ImportError, RuntimeError) as exc:
        return (ValidationIssue("error", "$.parameters.physics_backend", str(exc), "native_thruster_dependency"),)
    return ()


def _execute(case: _NativeCase):
    from components.fuel_tank.schemas import FuelTankConfig
    from components.thruster.schemas import ThrusterCommandConfig, ThrusterPhysicalConfig
    from subsystems.propulsion.builder import _DelayedBurnController, build_propulsion_basilisk_sim
    from subsystems.propulsion.schemas import PropulsionBasiliskConfig, PropulsionConfig

    burn_s = case.burn_ns / _NS_PER_S
    graph = build_propulsion_basilisk_sim(
        PropulsionConfig(
            thruster_command=ThrusterCommandConfig(
                num_thrusters=1, active_ids=(0,), nominal_on_time_s=burn_s,
                min_pulse_s=0.0, use_min_pulse_time=False,
            ),
            thruster_physical=ThrusterPhysicalConfig(
                thrust_n=(case.thrust_n,), isp_s=(case.isp_s,),
                directions_b=((1.0, 0.0, 0.0),), lever_arms_b_m=((0.0, 0.0, 0.0),),
            ),
            fuel_tank=FuelTankConfig(capacity_kg=_TANK_CAPACITY_KG, initial_mass_kg=_INITIAL_FUEL_KG),
        ),
        PropulsionBasiliskConfig(
            duration_s=case.duration_ns / _NS_PER_S, step_s=case.step_ns / _NS_PER_S,
            spacecraft_mass_kg=_HUB_MASS_KG, on_time_s=(0.0 if case.start_ns else burn_s,),
        ),
    )
    if case.start_ns:
        # The builder normally schedules this publisher after the effector. For
        # an exact one-step pulse the effector must see the command at its start.
        controller = _DelayedBurnController(case.start_ns, (burn_s,), graph.message_handles["thruster_command"])
        graph.simulation.AddModelToTask(graph.task_name, controller, ModelPriority=1)
    graph.simulation.InitializeSimulation()
    graph.simulation.ConfigureStopTime(case.duration_ns)
    graph.simulation.ExecuteSimulation()
    return graph


def _recording(recorders, name, field, expected_times):
    import numpy as np

    try:
        rec = recorders[name]
        times = np.asarray(rec.times())
        values = np.asarray(getattr(rec, field))
    except (KeyError, AttributeError) as exc:
        raise RuntimeError(f"missing native recorder {name}.{field}") from exc
    if times.dtype.kind not in "iu" or not np.array_equal(times, expected_times):
        raise RuntimeError(f"incomplete native recorder time grid for {name}.{field}")
    if values.shape != times.shape or values.dtype.kind not in "fiu" or not np.isfinite(values).all():
        raise RuntimeError(f"invalid native recorder values for {name}.{field}")
    return times, values


def run_native_thruster(spec: Mapping[str, Any], case: _NativeCase) -> SimulationResult:
    graph = _execute(case)
    import numpy as np

    expected = np.arange(0, case.duration_ns + 1, case.step_ns, dtype=np.int64)
    times, force = _recording(graph.recorders, "thruster_0", "thrustForce", expected)
    _, mass = _recording(graph.recorders, "fuel_tank", "fuelMass", expected)
    if (mass <= 0).any() or (np.diff(mass) > 0).any() or (force < 0).any():
        raise RuntimeError("native fixture recorded invalid propellant mass or thrust")
    # The effector records the thrust at the end of each elapsed physics step.
    impulse = np.r_[0.0, np.cumsum(force[1:] * (np.diff(times) / _NS_PER_S))]
    if not np.isfinite(impulse).all():
        raise RuntimeError("native thrust integral exceeds numerical range")
    used = mass[0] - mass
    residual = float(np.max(np.abs(used - impulse / (case.isp_s * G0))))
    if case.burn_ns and case.thrust_n > 0.0:
        if used[-1] <= 0.0 or impulse[-1] <= 0.0:
            raise RuntimeError("native conservation check requires a recorded fuel decrease and impulse for a positive burn")
        if residual > case.step_fuel_kg + case.rounding_budget_kg:
            raise RuntimeError("native fuel and impulse violate the switching and rounding conservation budget")
    elif used[-1] != 0.0 or impulse[-1] != 0.0:
        raise RuntimeError("native conservation check detected fuel use or impulse in a zero-force control")
    rows = []
    for index, i in enumerate(range(0, len(times), case.sample_ns // case.step_ns)):
        row = _row_base(spec, "thruster", index, int(times[i]) / _NS_PER_S, "nominal")
        row.update({
            _PREFIX + "on_time_s": case.commands_ns[index - 1] / _NS_PER_S if index else 0.0,
            _PREFIX + "effective_thrust_n": float(force[i]),
            _PREFIX + "effective_isp_s": case.isp_s,
            _PREFIX + "cumulative_impulse_ns": float(impulse[i]),
            _PREFIX + "cumulative_propellant_kg": float(used[i]),
            "label.fault_active": False, "label.degradation_active": False, "label.health_state": "nominal",
        })
        rows.append(row)

    qoi = {_PREFIX + "total_impulse_ns": float(impulse[-1]), _PREFIX + "propellant_used_kg": float(used[-1])}
    labels = {"task_id": rows[0]["task_id"], "mode": "nominal", "capability_id": _CAPABILITY}
    summary = {
        **labels, "case_id": rows[0]["case_id"], "status": "complete", "target_name": "thruster",
        "target_level": "component", "duration_s": case.duration_ns / _NS_PER_S,
        "sample_s": case.sample_ns / _NS_PER_S, "trace_rows": len(rows), "events": {}, "qoi": qoi,
    }
    metadata = {
        "capability_id": _CAPABILITY, "physics_backend": "basilisk_native",
        "native_provenance": "Basilisk.simulation.thrusterDynamicEffector.ThrusterDynamicEffector",
        "native_modules": ["thrusterDynamicEffector.ThrusterDynamicEffector", "fuelTank.FuelTank", "spacecraft.Spacecraft"],
        "native_fixture": {
            "spacecraft_mass_kg": _HUB_MASS_KG, "initial_propellant_kg": _INITIAL_FUEL_KG,
            "tank_capacity_kg": _TANK_CAPACITY_KG, "direction_b": [1, 0, 0], "lever_arm_b_m": [0, 0, 0],
        },
        "native_step_s": case.step_ns / _NS_PER_S, "native_recorder_samples": len(times),
        "burn_start_s": case.start_ns / _NS_PER_S, "burn_duration_s": case.burn_ns / _NS_PER_S,
        "max_fuel_conservation_residual_kg": residual,
        "field_sources": {
            _PREFIX + "on_time_s": "period command from TaskSpec, held at its final value",
            _PREFIX + "effective_isp_s": "configured nominal ThrusterPhysicalConfig.isp_s",
            _PREFIX + "effective_thrust_n": "recorded THROutputMsg.thrustForce",
            _PREFIX + "cumulative_impulse_ns": "right-endpoint integral of dense THROutputMsg.thrustForce",
            _PREFIX + "cumulative_propellant_kg": "initial minus current recorded FuelTankMsg.fuelMass",
            _PREFIX + "total_impulse_ns": "final cumulative_impulse_ns",
            _PREFIX + "propellant_used_kg": "final cumulative_propellant_kg",
        },
    }
    return SimulationResult(summary, tuple(rows), {"run_labels": [labels]}, metadata)
