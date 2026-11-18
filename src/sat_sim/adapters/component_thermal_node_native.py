"""Run the official SensorThermal model on a fixed, explicitly connected fixture."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from importlib.metadata import version as distribution_version
from typing import Any

from sat_sim.adapter_base import SimulationResult
from sat_sim.capability_backends import get_task_capability, validate_backend_output_requests
from sat_sim.task_models import NativeComponentInputError, validate_native_component_events, validate_native_component_timing
from sat_sim.task_validator import ValidationIssue

from .component_source_native_misc import _row_base


_CAPABILITY = "component.thermal_node.v1"
_TARGET = "thermal_node"
_BACKEND = "basilisk_native"
_PREFIX = "thermal.node."
_SOURCE = "Basilisk.simulation.sensorThermal.SensorThermal"
_REQUIRED_BASILISK_VERSION = "2.11.0"
_TRUTH_STATUS = "official_native_temperature_recording"
_NS_PER_S = 1_000_000_000
_MAX_STEPS = 100_000
_CELSIUS_OFFSET = 273.15
_SOLAR_FLUX_W_PER_M2 = 1366.0
_EMISSION_W_PER_M2_K4 = 5.76051e-8
_VECTOR_TOLERANCE = 1e-12
_MAX_ENVELOPE_K = 2000.0
_MAX_EULER_SLOPE = 0.1
# Basilisk 2.11.0 orbitalMotion.AU is 149597870.693 km.
_SUN_DISTANCE_M = 149_597_870_693.0
_DEVICE_STATUS = 1
_ZERO_VECTOR = (0.0, 0.0, 0.0)
_MESSAGE_BINDINGS = {
    "sunInMsg": "SpicePlanetStateMsg.PositionVector",
    "stateInMsg": "SCStatesMsg.r_BN_N, SCStatesMsg.sigma_BN",
    "sunEclipseInMsg": "EclipseMsg.illuminationFactor",
    "sensorStatusInMsg": "DeviceStatusMsg.deviceStatus",
    "temperatureOutMsg": "TemperatureMsg.temperature",
}


@dataclass(frozen=True)
class _NativeCase:
    duration_ns: int
    sample_ns: int
    step_ns: int
    initial_temp_k: float
    power_w: float
    surface_area_m2: float
    mass_kg: float
    specific_heat_j_per_kg_k: float
    absorptivity: float
    emissivity: float
    normal_b: tuple[float, float, float]
    sun_direction_n: tuple[float, float, float]
    illumination_factor: float
    envelope_k: float
    max_step_s: float
    known_physics_limits: tuple[str, ...]

    @property
    def initial_temp_c(self) -> float:
        return self.initial_temp_k - _CELSIUS_OFFSET

    @property
    def sun_position_n_m(self) -> list[float]:
        return [_SUN_DISTANCE_M * axis for axis in self.sun_direction_n]


def _mapping(value: Any, path: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NativeComponentInputError(path, "requires a mapping")
    return value


def _number(value: Any, path: str, minimum: float, maximum: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not minimum <= value <= maximum:
        raise NativeComponentInputError(path, f"requires a finite number in [{minimum:g}, {maximum:g}]")
    return float(value)


def _unit_vector(value: Any, path: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise NativeComponentInputError(path, "requires a three-component unit vector")
    limit = 1.0 + _VECTOR_TOLERANCE
    x, y, z = (_number(component, f"{path}[{index}]", -limit, limit) for index, component in enumerate(value))
    if not math.isclose(math.hypot(x, y, z), 1.0, rel_tol=0.0, abs_tol=_VECTOR_TOLERANCE):
        raise NativeComponentInputError(path, "requires a unit vector within absolute norm tolerance 1e-12")
    return x, y, z


def _time_ns(seconds: float, path: str) -> int:
    # Match Basilisk macros.sec2nano without loading native bindings in preflight.
    scaled = seconds * _NS_PER_S
    limit = 2**52
    if scaled >= limit:
        raise NativeComponentInputError(path, "time exceeds the supported nanosecond precision")
    ns = int(scaled + 0.5)
    if not 0 < ns < limit or int(ns / _NS_PER_S * _NS_PER_S + 0.5) != ns:
        raise NativeComponentInputError(path, "positive scheduling time must round-trip through integer nanoseconds below 2**52")
    return ns


def _schedule(simulation: Any) -> tuple[int, int, int, str]:
    validate_native_component_timing(simulation, require_duration_and_sample=True)
    duration_s, sample_s = float(simulation["duration_s"]), float(simulation["sample_s"])
    if sample_s > duration_s:
        raise NativeComponentInputError("$.simulation.sample_s", "sample_s must not exceed duration_s")
    duration_ns = _time_ns(duration_s, "$.simulation.duration_s")
    sample_ns = _time_ns(sample_s, "$.simulation.sample_s")

    solver = simulation.get("solver") or {}
    if solver.get("method", "euler") != "euler":
        raise NativeComponentInputError("$.simulation.solver.method", "native thermal node uses explicit Euler (method='euler')")
    step_ns, step_path = None, "$.simulation.sample_s"
    for source, path in ((simulation, "$.simulation.step_s"), (solver, "$.simulation.solver.step_s")):
        if "step_s" not in source:
            continue
        step_s = float(source["step_s"])
        if step_s > sample_s:
            raise NativeComponentInputError(path, "integration step must not exceed sample_s")
        quantized = _time_ns(step_s, path)
        if step_ns is not None and step_ns != quantized:
            raise NativeComponentInputError(path, "integration steps must agree after nanosecond rounding")
        if step_ns is None:
            step_ns, step_path = quantized, path
    if step_ns is None:
        step_ns = sample_ns
    if duration_ns % sample_ns or sample_ns % step_ns:
        raise NativeComponentInputError("$.simulation", "duration, sample and step require complete positive intervals")
    if duration_ns // step_ns > _MAX_STEPS:
        raise NativeComponentInputError("$.simulation", f"native thermal node exceeds {_MAX_STEPS} integration steps")
    return duration_ns, sample_ns, step_ns, step_path


def _compile_case(spec: Mapping[str, Any]) -> _NativeCase:
    if spec.get("capability_id") != _CAPABILITY:
        raise NativeComponentInputError("$.capability_id", f"must be {_CAPABILITY!r}")
    if spec.get("task_type") != "component":
        raise NativeComponentInputError("$.task_type", "native thermal node requires task_type='component'")
    target = _mapping(spec.get("target"), "$.target")
    if target.get("level") != "component" or target.get("name") != _TARGET:
        raise NativeComponentInputError("$.target", "requires target.level='component' and target.name='thermal_node'")
    if target.get("mode", "nominal") != "nominal":
        raise NativeComponentInputError("$.target.mode", "native thermal node supports nominal mode only")
    mission = _mapping(spec.get("mission", {}), "$.mission")
    if mission.get("required_couplings", []) != []:
        raise NativeComponentInputError("$.mission.required_couplings", "native thermal node supports no physical couplings")
    validate_native_component_events(spec, component_name="native thermal node")

    parameters = _mapping(spec.get("parameters"), "$.parameters")
    if parameters.get("physics_backend") != _BACKEND:
        raise NativeComponentInputError("$.parameters.physics_backend", f"must be {_BACKEND!r}")
    contract = get_task_capability(spec)
    definitions = contract.data["parameters"]
    for name in parameters:
        if name not in definitions:
            raise NativeComponentInputError(f"$.parameters.{name}", "parameter is unavailable for native thermal node")

    duration_ns, sample_ns, step_ns, step_path = _schedule(spec.get("simulation"))

    def scalar(name: str) -> float:
        definition = definitions[name]
        return _number(parameters.get(name, definition["default"]), f"$.parameters.{name}", definition["min"], definition["max"])

    initial = scalar("initial_temp_k")
    power = scalar("power_w")
    area = scalar("surface_area_m2")
    mass = scalar("mass_kg")
    specific_heat = scalar("specific_heat_j_per_kg_k")
    absorb = scalar("absorptivity")
    emit = scalar("emissivity")
    illumination = scalar("illumination_factor")
    normal = _unit_vector(parameters.get("normal_b", definitions["normal_b"]["default"]), "$.parameters.normal_b")
    sun = _unit_vector(parameters.get("sun_direction_n", definitions["sun_direction_n"]["default"]), "$.parameters.sun_direction_n")

    capacity = mass * specific_heat
    radiation = _EMISSION_W_PER_M2_K4 * area * emit
    heating_upper = power + _SOLAR_FLUX_W_PER_M2 * area * absorb * illumination * (1.0 + _VECTOR_TOLERANCE)
    if not (0 < capacity < math.inf and 0 < radiation < math.inf and 0 <= heating_upper < math.inf):
        raise NativeComponentInputError("$.parameters", "native heat capacity, radiation and heating must be finite")
    nominal_envelope = max(initial, (heating_upper / radiation) ** 0.25)
    if not 0.0 < nominal_envelope <= _MAX_ENVELOPE_K:
        raise NativeComponentInputError("$.parameters", f"native temperature envelope must be at most {_MAX_ENVELOPE_K:g} K")
    envelope = math.nextafter(nominal_envelope, math.inf)
    # On this positive envelope Euler remains monotone, preserves positivity,
    # and cannot heat past the upper endpoint. This bound is independent of the trajectory.
    slope = 4.0 * radiation * envelope**3 / capacity
    max_step_s = _MAX_EULER_SLOPE / slope
    if (step_ns / _NS_PER_S) * slope > _MAX_EULER_SLOPE:
        raise NativeComponentInputError(step_path, f"native Euler integration step must not exceed {max_step_s:.12g} s for the temperature envelope")

    output_issues = validate_backend_output_requests(spec, contract)
    if output_issues:
        issue = output_issues[0]
        raise NativeComponentInputError(issue.path, issue.message)
    return _NativeCase(duration_ns, sample_ns, step_ns, initial, power, area, mass, specific_heat,
                       absorb, emit, normal, sun, illumination, envelope, max_step_s,
                       tuple(contract.data["known_physics_limits"]))


def _native_bindings():
    try:
        import Basilisk
        from Basilisk.architecture import messaging
        from Basilisk.simulation import sensorThermal
        from Basilisk.utilities import SimulationBaseClass

        installed = distribution_version("bsk")
        if Basilisk.__version__ != _REQUIRED_BASILISK_VERSION or installed != _REQUIRED_BASILISK_VERSION:
            raise ImportError(
                f"SensorThermal preflight requires bsk/Basilisk {_REQUIRED_BASILISK_VERSION} "
                f"(found bsk={installed}, Basilisk={Basilisk.__version__})"
            )
        return installed, sensorThermal.__file__, sensorThermal.SensorThermal, messaging, SimulationBaseClass.SimBaseClass
    except (ImportError, AttributeError, OSError) as exc:
        raise RuntimeError(f"Basilisk native thermal node is unavailable: {exc}") from exc


def validate_native_thermal_node(spec: Mapping[str, Any]) -> Sequence[ValidationIssue]:
    try:
        _compile_case(spec)
        _native_bindings()
    except NativeComponentInputError as exc:
        code = "MISSING_PHYSICAL_CAUSAL_LINK" if exc.path == "$.mission.required_couplings" else "native_thermal_node"
        return (ValidationIssue("error", exc.path, exc.message, code),)
    except RuntimeError as exc:
        return (ValidationIssue("error", "$.parameters.physics_backend", str(exc), "native_thermal_node_dependency"),)
    return ()


def _execute(case: _NativeCase):
    version, module_path, model_class, messaging, sim_class = _native_bindings()
    sim = sim_class()
    task_name = "thermalNodeComponentTask"
    process = sim.CreateNewProcess("thermalNodeComponentProcess")
    process.addTask(sim.CreateNewTask(task_name, case.step_ns))

    model = model_class()
    model.ModelTag = "thermalNodeComponent"
    model.T_0 = case.initial_temp_c
    model.sensorPowerDraw = case.power_w
    model.sensorArea = case.surface_area_m2
    model.sensorMass = case.mass_kg
    model.sensorSpecificHeat = case.specific_heat_j_per_kg_k
    model.sensorAbsorptivity = case.absorptivity
    model.sensorEmissivity = case.emissivity
    model.nHat_B = list(case.normal_b)
    model.sensorPowerStatus = _DEVICE_STATUS

    sun_payload = messaging.SpicePlanetStateMsgPayload()
    sun_payload.PositionVector = case.sun_position_n_m
    sun = messaging.SpicePlanetStateMsg().write(sun_payload)
    model.sunInMsg.subscribeTo(sun)

    state_payload = messaging.SCStatesMsgPayload()
    state_payload.r_BN_N = list(_ZERO_VECTOR)
    state_payload.sigma_BN = list(_ZERO_VECTOR)
    state = messaging.SCStatesMsg().write(state_payload)
    model.stateInMsg.subscribeTo(state)

    eclipse_payload = messaging.EclipseMsgPayload()
    eclipse_payload.illuminationFactor = case.illumination_factor
    eclipse = messaging.EclipseMsg().write(eclipse_payload)
    model.sunEclipseInMsg.subscribeTo(eclipse)

    status_payload = messaging.DeviceStatusMsgPayload()
    status_payload.deviceStatus = _DEVICE_STATUS
    status = messaging.DeviceStatusMsg().write(status_payload)
    model.sensorStatusInMsg.subscribeTo(status)

    recorder = model.temperatureOutMsg.recorder()
    sim.AddModelToTask(task_name, model, 20)
    sim.AddModelToTask(task_name, recorder, 10)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(case.duration_ns)
    sim.ExecuteSimulation()
    return version, module_path, recorder


def _checked_recording(case: _NativeCase, recorder: Any):
    import numpy as np

    expected = np.arange(0, case.duration_ns + 1, case.step_ns, dtype=np.int64)
    try:
        times = np.asarray(recorder.times())
        celsius = np.asarray(recorder.temperature)
        written_times = np.asarray(recorder.timesWritten())
    except (AttributeError, TypeError, ValueError) as exc:
        raise RuntimeError("missing or malformed native temperatureOutMsg recorder data") from exc
    for timestamps in (times, written_times):
        if timestamps.dtype.kind not in "iu" or not np.array_equal(timestamps, expected):
            raise RuntimeError("incomplete native temperatureOutMsg recorder time grid")
    if celsius.shape != expected.shape or celsius.dtype.kind not in "fiu" or not np.isfinite(celsius).all():
        raise RuntimeError("invalid native temperatureOutMsg recorder temperatures")
    kelvin = celsius.astype(np.float64) + _CELSIUS_OFFSET
    tolerance = 64 * math.ulp(max(case.envelope_k, _CELSIUS_OFFSET))
    if celsius[0] != case.initial_temp_c or (kelvin <= 0).any() or (kelvin > case.envelope_k + tolerance).any():
        raise RuntimeError("native temperatureOutMsg recorder has an unexpected initial or out-of-envelope temperature")
    return times, celsius, kelvin


def run_native_thermal_node(spec: Mapping[str, Any]) -> SimulationResult:
    case = _compile_case(spec)
    version, module_path, recorder = _execute(case)
    times, celsius, kelvin = _checked_recording(case, recorder)
    rows = []
    for index, tick in enumerate(range(0, len(times), case.sample_ns // case.step_ns)):
        row = _row_base(spec, _TARGET, index, int(times[tick]) / _NS_PER_S, "nominal")
        row.update({
            _PREFIX + "temp_c": float(celsius[tick]),
            _PREFIX + "temp_k": float(kelvin[tick]),
            _PREFIX + "input_power_w": case.power_w,
            "label.health_state": "nominal",
        })
        rows.append(row)

    initial, final = float(kelvin[0]), float(kelvin[-1])
    qoi = {
        _PREFIX + "initial_temp_k": initial,
        _PREFIX + "final_temp_k": final,
        _PREFIX + "min_temp_k": float(kelvin.min()),
        _PREFIX + "max_temp_k": float(kelvin.max()),
        _PREFIX + "delta_temp_k": final - initial,
    }
    labels = {
        "task_id": rows[0]["task_id"], "mode": "nominal", "capability_id": _CAPABILITY, "physics_backend": _BACKEND,
    }
    summary = {
        **labels, "case_id": rows[0]["case_id"], "status": "complete", "runtime_truth_status": _TRUTH_STATUS,
        "target_name": _TARGET, "target_level": "component", "duration_s": case.duration_ns / _NS_PER_S,
        "sample_s": case.sample_ns / _NS_PER_S, "trace_rows": len(rows), "events": {}, "qoi": qoi,
    }
    metadata = {
        "capability_id": _CAPABILITY,
        "physics_backend": _BACKEND,
        "native_provenance": _SOURCE,
        "basilisk_version": version,
        "native_module_path": module_path,
        "runtime_truth_status": _TRUTH_STATUS,
        "message_bindings": dict(_MESSAGE_BINDINGS),
        "native_fixture": {
            "initial_temp_k": case.initial_temp_k,
            "T_0_c": case.initial_temp_c,
            "power_w": case.power_w,
            "surface_area_m2": case.surface_area_m2,
            "mass_kg": case.mass_kg,
            "specific_heat_j_per_kg_k": case.specific_heat_j_per_kg_k,
            "absorptivity": case.absorptivity,
            "emissivity": case.emissivity,
            "normal_b": list(case.normal_b),
            "sun_direction_n": list(case.sun_direction_n),
            "sun_distance_m": _SUN_DISTANCE_M,
            "sun_position_n_m": case.sun_position_n_m,
            "spacecraft_position_n_m": list(_ZERO_VECTOR),
            "spacecraft_sigma_bn": list(_ZERO_VECTOR),
            "illumination_factor": case.illumination_factor,
            "device_status": _DEVICE_STATUS,
        },
        "native_model_constants": {
            "solar_flux_w_per_m2": _SOLAR_FLUX_W_PER_M2,
            "emission_constant_w_per_m2_k4": _EMISSION_W_PER_M2_K4,
        },
        "temperature_conversion": {"input": "T_0 = initial_temp_k - 273.15", "output": "K = recorded Celsius + 273.15"},
        "known_physics_limits": list(case.known_physics_limits),
        "native_step_s": case.step_ns / _NS_PER_S,
        "native_recorder_samples": len(times),
        "native_temperature_envelope_k": case.envelope_k,
        "native_max_step_s": case.max_step_s,
        "field_sources": {
            _PREFIX + "temp_c": "recorded temperatureOutMsg.temperature in Celsius",
            _PREFIX + "temp_k": "recorded temperatureOutMsg.temperature plus 273.15",
            _PREFIX + "input_power_w": "configured SensorThermal.sensorPowerDraw electrical command",
            _PREFIX + "initial_temp_k": "first recorded temperature converted to Kelvin",
            _PREFIX + "final_temp_k": "last recorded temperature converted to Kelvin",
            _PREFIX + "min_temp_k": "minimum of all validated dense recorded temperatures converted to Kelvin",
            _PREFIX + "max_temp_k": "maximum of all validated dense recorded temperatures converted to Kelvin",
            _PREFIX + "delta_temp_k": "last minus first recorded temperature after conversion to Kelvin",
        },
    }
    return SimulationResult(summary, tuple(rows), {"run_labels": [labels]}, metadata)
