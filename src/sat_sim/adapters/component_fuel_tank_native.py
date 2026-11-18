"""Official Basilisk FuelTank on a free carrier with prescribed mass withdrawal."""
from __future__ import annotations

import math
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.capability_backends import get_task_capability, validate_backend_output_requests
from sat_sim.task_models import NativeFuelInputError, validate_native_fuel_events, validate_native_fuel_timing
from sat_sim.task_validator import ValidationIssue

from .component_source_native_misc import _row_base


_CAPABILITY = "component.fuel_tank.v1"
_TARGET = "fuel_tank"
_BACKEND = "basilisk_native"
_PREFIX = "propulsion.fuel_tank."
_NS_PER_S = 1_000_000_000
_MAX_STEPS = 100_000
_HUB_MASS_KG = 10.0
_TANK_RADIUS_M = 0.1
_ZERO_OFFSET_M = (0.0, 0.0, 0.0)
_IDENTITY = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
_RECORDED_FIELDS = {
    "propellant_mass_kg": "fuelMass",
    "mass_rate_kg_s": "fuelMassDot",
    "max_mass_kg": "maxFuelMass",
}


class _InputError(ValueError):
    def __init__(self, path: str, message: str, code: str = "native_fuel_tank"):
        super().__init__(message)
        self.issue = ValidationIssue("error", path, message, code)


@dataclass(frozen=True)
class _NativeCase:
    duration_ns: int
    sample_ns: int
    step_ns: int
    capacity_kg: float
    initial_mass_kg: float
    flow_kg_s: float

    @property
    def rounding_budget_kg(self) -> float:
        steps = self.duration_ns // self.step_ns
        return 2 * (steps + 2) * math.ulp(self.initial_mass_kg)


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _InputError(path, "requires a mapping")
    return value


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


def _time_ns(seconds: float, path: str) -> int:
    scaled = seconds * _NS_PER_S
    # Basilisk sec2nano rounds by adding 0.5 before integer conversion.
    if scaled >= 2**52:
        raise _InputError(path, "time exceeds the supported nanosecond precision")
    ns = int(scaled + 0.5)
    if not ns or int(ns / _NS_PER_S * _NS_PER_S + 0.5) != ns:
        raise _InputError(path, "positive scheduling time must round-trip through integer nanoseconds")
    return ns


def _compile_case(spec: Mapping[str, Any]) -> _NativeCase:
    if spec.get("capability_id") != _CAPABILITY:
        raise _InputError("$.capability_id", f"must be {_CAPABILITY!r}")
    if spec.get("task_type") != "component":
        raise _InputError("$.task_type", "native fuel tank requires task_type='component'")
    target = _mapping(spec.get("target"), "$.target")
    if target.get("level") != "component" or target.get("name") != _TARGET:
        raise _InputError("$.target", "requires target.level='component' and target.name='fuel_tank'")
    if target.get("mode", "nominal") != "nominal":
        raise _InputError("$.target.mode", "native fuel tank supports nominal mode only")
    mission = _mapping(spec.get("mission", {}), "$.mission")
    if mission.get("required_couplings", []) != []:
        raise _InputError(
            "$.mission.required_couplings", "native fuel tank does not provide required physical couplings",
            "MISSING_PHYSICAL_CAUSAL_LINK",
        )
    validate_native_fuel_events(spec)

    params = _mapping(spec.get("parameters"), "$.parameters")
    if params.get("physics_backend") != _BACKEND:
        raise _InputError("$.parameters.physics_backend", f"must be {_BACKEND!r}")
    contract = get_task_capability(spec)
    definitions = contract.data["parameters"]
    for name in params:
        if name not in definitions:
            raise _InputError(f"$.parameters.{name}", "parameter is unavailable for native fuel tank")

    sim = _mapping(spec.get("simulation"), "$.simulation")
    validate_native_fuel_timing(sim, require_duration_and_sample=True)
    duration_path, sample_path = "$.simulation.duration_s", "$.simulation.sample_s"
    duration_s = float(sim["duration_s"])
    sample_s = float(sim["sample_s"])
    if sample_s > duration_s:
        raise _InputError(sample_path, "sample_s must not exceed duration_s")
    duration_ns = _time_ns(duration_s, duration_path)
    sample_ns = _time_ns(sample_s, sample_path)

    solver = sim.get("solver") or {}
    step_ns = None
    for source, path in ((sim, "$.simulation.step_s"), (solver, "$.simulation.solver.step_s")):
        if "step_s" not in source:
            continue
        step_s = float(source["step_s"])
        if step_s > sample_s:
            raise _InputError(path, "integration step must not exceed sample_s")
        explicit_step_ns = _time_ns(step_s, path)
        if step_ns is not None and step_ns != explicit_step_ns:
            raise _InputError(path, "integration steps must agree after nanosecond rounding")
        step_ns = explicit_step_ns
    step_ns = step_ns if step_ns is not None else sample_ns
    if duration_ns % sample_ns or sample_ns % step_ns:
        raise _InputError("$.simulation", "duration, sample and step require complete positive intervals")
    if duration_ns // step_ns > _MAX_STEPS:
        raise _InputError("$.simulation", f"native fuel tank exceeds {_MAX_STEPS} integration steps")

    def quantity(name: str) -> float:
        definition = definitions[name]
        return _number(params.get(name, definition["default"]), f"$.parameters.{name}", definition["min"])

    capacity = quantity("capacity_kg")
    initial_mass = quantity("initial_mass_kg")
    flow = quantity("mass_flow_kg_s")
    if initial_mass > capacity:
        raise _InputError("$.parameters.initial_mass_kg", "initial mass must not exceed tank capacity")
    case = _NativeCase(duration_ns, sample_ns, step_ns, capacity, initial_mass, flow)
    if flow > 0.0:
        flow_path = "$.parameters.mass_flow_kg_s"
        withdrawal_with_reserve = flow * ((duration_ns + step_ns) / _NS_PER_S)
        if withdrawal_with_reserve >= initial_mass:
            raise _InputError(flow_path, "withdrawal requires at least one native step of mass reserve")
        # Basilisk 2.11.0 multiplies the remaining tank mass by the flow when
        # deriving the tank's share of withdrawal. Bound that intermediate at
        # the initial mass and at the lower mass including the stage reserve.
        if not math.isfinite(initial_mass * flow) or (initial_mass - withdrawal_with_reserve) * flow < sys.float_info.min:
            raise _InputError(flow_path, "native tank mass-times-flow intermediate must stay finite and normal")
        if flow * (step_ns / _NS_PER_S) <= case.rounding_budget_kg:
            raise _InputError(flow_path, "withdrawal per native step must exceed the accumulated mass rounding budget")

    issues = validate_backend_output_requests(spec, contract)
    if issues:
        issue = issues[0]
        raise _InputError(issue.path, issue.message, issue.code)
    return case


def _native_bindings():
    try:
        import Basilisk
        from Basilisk.architecture import messaging
        from Basilisk.simulation import fuelTank, spacecraft
        from Basilisk.utilities import SimulationBaseClass

        if not hasattr(fuelTank.FuelTank, "fuelLeakRateInMsg") or not hasattr(messaging, "MassFlowRateMsg"):
            raise ImportError("FuelTank prescribed mass withdrawal requires fuelLeakRateInMsg and MassFlowRateMsg")
        return Basilisk.__version__, SimulationBaseClass.SimBaseClass, spacecraft.Spacecraft
    except (ImportError, AttributeError, OSError) as exc:
        raise RuntimeError(f"Basilisk native fuel tank is unavailable: {exc}") from exc


def validate_native_fuel_tank(spec: Mapping[str, Any]) -> Sequence[ValidationIssue]:
    try:
        _compile_case(spec)
        _native_bindings()
    except _InputError as exc:
        return (exc.issue,)
    except NativeFuelInputError as exc:
        return (ValidationIssue("error", exc.path, exc.message, "native_fuel_tank"),)
    except RuntimeError as exc:
        return (ValidationIssue("error", "$.parameters.physics_backend", str(exc), "native_fuel_tank_dependency"),)
    return ()


def _execute(case: _NativeCase):
    version, sim_class, spacecraft_class = _native_bindings()

    from components.fuel_tank.builder import attach_fuel_tank_to_spacecraft, build_fuel_tank_bundle_from_config
    from components.fuel_tank.schemas import FuelTankConfig

    sim = sim_class()
    task_name = "fuelTankComponentTask"
    process = sim.CreateNewProcess("fuelTankComponentProcess")
    process.addTask(sim.CreateNewTask(task_name, case.step_ns))

    carrier = spacecraft_class()
    carrier.ModelTag = "fuelTankFreeCarrier"
    carrier.hub.mHub = _HUB_MASS_KG
    carrier.hub.IHubPntBc_B = [list(row) for row in _IDENTITY]
    bundle = build_fuel_tank_bundle_from_config(
        "fuelTankComponent",
        FuelTankConfig(
            capacity_kg=case.capacity_kg,
            initial_mass_kg=case.initial_mass_kg,
            leak_rate_kg_s=case.flow_kg_s,
            tank_model="constant_volume",
            radius_tank_m=_TANK_RADIUS_M,
            r_tb_b_m=_ZERO_OFFSET_M,
            dcm_tb=_IDENTITY,
        ),
    )
    attach_fuel_tank_to_spacecraft(bundle.tank, carrier)
    recorder = bundle.tank.fuelTankOutMsg.recorder(case.step_ns)
    sim.AddModelToTask(task_name, carrier)
    sim.AddModelToTask(task_name, bundle.tank)
    sim.AddModelToTask(task_name, recorder)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(case.duration_ns)
    sim.ExecuteSimulation()
    return version, recorder


def _checked_recording(case: _NativeCase, recorder):
    import numpy as np

    expected = np.arange(0, case.duration_ns + 1, case.step_ns, dtype=np.int64)
    try:
        times = np.asarray(recorder.times())
        fields = {name: np.asarray(getattr(recorder, payload)) for name, payload in _RECORDED_FIELDS.items()}
    except AttributeError as exc:
        raise RuntimeError("missing fields in native fuelTankOutMsg recorder") from exc
    if times.dtype.kind not in "iu" or not np.array_equal(times, expected):
        raise RuntimeError("incomplete native fuelTankOutMsg recorder time grid")
    for name, values in fields.items():
        if values.shape != times.shape or values.dtype.kind not in "fiu" or not np.isfinite(values).all():
            raise RuntimeError(f"invalid native fuelTankOutMsg recorder values for {_RECORDED_FIELDS[name]}")

    mass = fields["propellant_mass_kg"]
    if mass[0] != case.initial_mass_kg or (mass < 0.0).any() or (mass > case.capacity_kg).any():
        raise RuntimeError("native fuel tank recorded unexpected initial mass or out-of-bounds mass")
    if not (fields["mass_rate_kg_s"] == -case.flow_kg_s).all():
        raise RuntimeError("native fuel tank recorded an inconsistent signed mass derivative")
    if not (fields["max_mass_kg"] == case.capacity_kg).all():
        raise RuntimeError("native fuel tank recorded an inconsistent maximum mass")
    if case.flow_kg_s > 0.0:
        if mass[-1] <= 0.0 or (mass[1:] >= mass[:-1]).any():
            raise RuntimeError("native fuel tank requires positive mass and resolved withdrawal at every step")
    elif not (mass == mass[0]).all():
        raise RuntimeError("native fuel tank mass changed in a zero-withdrawal control")

    # The independent conservation oracle checks every recorded step, including
    # steps omitted from the exported trace.
    residual = float(np.max(np.abs((mass[0] - mass) - case.flow_kg_s * (times / _NS_PER_S))))
    if residual > case.rounding_budget_kg:
        raise RuntimeError("native fuel tank exceeds the dense mass conservation rounding budget")
    return times, fields, residual


def run_native_fuel_tank(spec: Mapping[str, Any]) -> SimulationResult:
    case = _compile_case(spec)
    version, recorder = _execute(case)
    times, fields, residual = _checked_recording(case, recorder)
    rows = []
    for index, i in enumerate(range(0, len(times), case.sample_ns // case.step_ns)):
        row = _row_base(spec, _TARGET, index, int(times[i]) / _NS_PER_S, "nominal")
        row.update({_PREFIX + name: float(values[i]) for name, values in fields.items()})
        row.update({
            _PREFIX + "commanded_mass_flow_kg_s": case.flow_kg_s,
            "label.fault_active": False,
            "label.degradation_active": False,
            "label.health_state": "nominal",
        })
        rows.append(row)

    mass = fields["propellant_mass_kg"]
    initial, final = float(mass[0]), float(mass[-1])
    qoi = {
        _PREFIX + "initial_mass_kg": initial,
        _PREFIX + "final_mass_kg": final,
        _PREFIX + "consumed_mass_kg": initial - final,
    }
    labels = {
        "task_id": rows[0]["task_id"], "mode": "nominal", "capability_id": _CAPABILITY, "physics_backend": _BACKEND,
    }
    summary = {
        **labels, "case_id": rows[0]["case_id"], "status": "complete", "target_name": _TARGET,
        "target_level": "component", "duration_s": case.duration_ns / _NS_PER_S,
        "sample_s": case.sample_ns / _NS_PER_S, "trace_rows": len(rows), "events": {}, "qoi": qoi,
    }
    metadata = {
        "capability_id": _CAPABILITY,
        "physics_backend": _BACKEND,
        "basilisk_version": version,
        "native_provenance": "Basilisk.simulation.fuelTank.FuelTank",
        "native_modules": ["fuelTank.FuelTank", "fuelTank.FuelTankModelConstantVolume", "spacecraft.Spacecraft"],
        "native_fixture": {
            "spacecraft_mass_kg": _HUB_MASS_KG,
            "hub_inertia_kg_m2": [list(row) for row in _IDENTITY],
            "tank_model": "constant_volume",
            "tank_radius_m": _TANK_RADIUS_M,
            "tank_offset_b_m": list(_ZERO_OFFSET_M),
            "tank_dcm_tb": [list(row) for row in _IDENTITY],
            "scope": "free carrier mass-withdrawal fixture",
        },
        "native_step_s": case.step_ns / _NS_PER_S,
        "native_recorder_samples": len(times),
        "max_fuel_conservation_residual_kg": residual,
        "field_sources": {
            **{_PREFIX + name: f"recorded fuelTankOutMsg.{field}" for name, field in _RECORDED_FIELDS.items()},
            _PREFIX + "commanded_mass_flow_kg_s": "configured MassFlowRateMsg.massFlowRate input to FuelTank.fuelLeakRateInMsg",
            _PREFIX + "initial_mass_kg": "first recorded fuelTankOutMsg.fuelMass",
            _PREFIX + "final_mass_kg": "last recorded fuelTankOutMsg.fuelMass",
            _PREFIX + "consumed_mass_kg": "first minus last recorded fuelTankOutMsg.fuelMass",
        },
    }
    return SimulationResult(summary, tuple(rows), {"run_labels": [labels]}, metadata)
