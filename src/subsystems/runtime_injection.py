"""Basilisk direct runtime event injection for subsystem fault/degradation specs.

The subsystem fault/degradation modules emit metadata that says *when* a
component-local mechanism should be injected and *where* it should land in a
Basilisk graph.  This module consumes that metadata and registers Basilisk
``createNewEvent`` callbacks that mutate live, real Basilisk module attributes
or command messages during ``ExecuteSimulation()``.

Direct-only policy: this module no longer writes subsystem-side
scalar target modules.  If a spec cannot be mapped to a writable Basilisk
module/SysModel/command message that is already part of the builder context, the
spec is reported as unsupported and is not registered as a default runtime event.
"""
from __future__ import annotations
from utils.runtime_diagnostics import DiagnosticCategory, record_runtime_diagnostic

from dataclasses import asdict, dataclass, field, replace
from typing import Any, Iterable, Mapping, Sequence


@dataclass(frozen=True)
class RuntimeInjectionRecord:
    """Serializable trace row for one runtime injection action."""

    time_s: float
    event_name: str
    scenario: str
    phase: str
    component: str
    target_id: str
    injection: str
    module_key: str
    parameter: str
    applied_to_basilisk: bool
    before: Mapping[str, Any] = field(default_factory=dict)
    after: Mapping[str, Any] = field(default_factory=dict)
    effects: tuple[Mapping[str, Any], ...] = ()
    note: str = ""


_KNOWN_MODULE_ALIASES: Mapping[str, Mapping[str, tuple[str, ...]]] = {
    "adcs": {
        "reactionWheelStateEffector": ("reaction_wheel_effector", "reaction_wheel_bundle"),
        "reactionWheelStateEffector.ReactionWheelData": ("reaction_wheel_bundle",),
    },
    "eps": {
        "simpleBattery": ("battery",),
        "simpleSolarPanel_or_fallback_source": ("solar_power_node",),
        "simpleSolarPanel": ("solar_power_node",),
        "fallback_source": ("solar_power_node",),
        "pdu": ("pdu",),
        "simplePowerSink": ("payload", "adcs", "comm", "heater", "bus", "solar_power_node"),
    },
    "comm_data": {
        "simpleTransmitter": ("transmitter",),
        "simpleStorageUnit": ("storage",),
        "simpleAntenna": ("spacecraft_antenna", "ground_antenna", "antenna"),
        "linkBudget": ("link_budget",),
        "downlinkHandling": ("downlink",),
        "groundLocation": ("ground_station",),
    },
    "payload": {
        "payload_power_data_gate": ("instrument",),
    },
    "propulsion": {
        "thruster_effector": ("thruster_bundle", "thruster_effector"),
        "fuel_tank": ("fuel_tank", "fuel_tank_bundle"),
    },
    "thermal": {
        "thermalScheduledSysModel": ("thermal_node", "heat_input"),
        "scheduled_thermal_node": ("thermal_node",),
        "constant_heat_input": ("heat_input",),
    },
}


_EFFECT_TO_ATTR: Mapping[str, tuple[str, ...]] = {
    # EPS / data-handling Basilisk writable fields.
    "available_current_factor": ("storageCapacity",),
    "capacity_ah": ("storageCapacity",),
    "output_power_w": ("nodePowerOut",),
    "string_availability": ("nodePowerOut",),
    "load_power_w": ("nodePowerOut",),
    "surge_power_w": ("nodePowerOut",),
    "trip_state": ("channel_enabled",),
    "data_rate_mbps": ("nodeBaudRate",),
    "data_rate": ("nodeBaudRate",),
    "instrument_enabled": ("nodeBaudRate",),
    "sensitivity_factor": ("scaleFactor", "nodeBaudRate"),
    "responsivity": ("nodeBaudRate",),
    "image_quality": ("nodeBaudRate",),
    "read_rate_bps": ("nodeBaudRate",),
    "write_rate_bps": ("nodeBaudRate",),
    "write_rate_mbps": ("nodeBaudRate",),
    "capacity_gb": ("storageCapacity",),
    "storage_capacity_wh": ("storageCapacity",),
    "capacity_wh": ("storageCapacity",),
    "storageCapacity": ("storageCapacity",),
    "storage_capacity_bits": ("storageCapacity",),
    "storage_capacity": ("storageCapacity",),
    "initial_storage_bits": ("storedData",),
    "stored_charge": ("storedCharge_Init",),
    "storageLevel": ("storedCharge_Init",),
    "netPower": ("nodePowerOut",),
    "net_power_w": ("nodePowerOut",),
    "nodePowerOut": ("nodePowerOut",),
    "load_demand": ("nodePowerOut",),
    "efficiency": ("nodePowerOut", "heat_power_w"),
    "data_rate_bps": ("nodeBaudRate",),
    "baudRate": ("nodeBaudRate",),
    "baud_rate_bps": ("nodeBaudRate",),
    "outputPower": ("transmitPower", "nodeBaudRate"),
    "rf_power_w": ("nodeBaudRate",),
    "pa_efficiency": ("nodeBaudRate",),
    "write_acceptance": ("nodeBaudRate",),
    "write_read_throughput": ("nodeBaudRate",),
    "throughput": ("nodeBaudRate",),
    # Thermal scheduled node / heat source fields.
    "heater_power_w": ("cfg.heater_power_w",),
    "cooling_power_w": ("cfg.cooling_power_w",),
    "rejection_factor": ("cfg.cooling_power_w",),
    "emissivity": ("cfg.cooling_power_w",),
    "heat_input_w": ("heat_power_w",),
    "temperature_k": ("temp_c",),
    "sensor_bias_k": ("temp_c",),
    "thermal_resistance_k_w": ("cfg.conductance_w_per_c",),
    "thermal_resistance": ("cfg.conductance_w_per_c",),
    "conductance": ("cfg.conductance_w_per_c",),
    "heat_capacity_j_k": ("cfg.thermal_capacity_j_per_c",),
    "heat_capacity": ("cfg.thermal_capacity_j_per_c",),
    # Command messages and direct module/SysModel writable fields.
    "thrust_n": ("thruster_command_scale",),
    "mass_flow_factor": ("thruster_command_scale",),
    "valve_opening": ("thruster_command_scale",),
    "max_torque_nm": ("rw_command_scale",),
    "availability": ("rw_command_scale",),
    "current_factor": ("mtb_command_scale",),
    "magnetic_moment_factor": ("mtb_command_scale", "magnetic_moment_factor"),
    "friction_torque_nm": ("friction_torque_nm",),
    "speed_sensor_bias_rad_s": ("speed_sensor_bias_rad_s",),
    # Sensor fields are only direct if the real Basilisk sensor exposes them.
    "bias_rad_s": ("senRotBias", "bias_rad_s"),
    "noise_sigma_rad_s": ("PMatrixGyro", "PMatrixAccel", "senNoiseStd", "noise_sigma_rad_s"),
    "attitude_sigma_arcsec": ("PMatrix", "attitude_sigma_arcsec"),
    "boresight_bias_arcsec": ("walkBounds", "boresight_bias_arcsec"),
    "dropout_probability": ("dropout_probability",),
    "bias_deg": ("senBias", "bias_deg"),
    "bias_nt": ("senBias", "bias_nt"),
    "noise_sigma_nt": ("senNoiseStd", "noise_sigma_nt"),
    "sun_vector_sigma_deg": ("senNoiseStd", "faultNoiseStd", "sun_vector_sigma_deg"),
    "axis_bias": ("senBias", "axis_bias"),
    "scale_factor": ("scaleFactor", "scale_factor"),
    "gimbal_rate_limit_rad_s": ("gimbal_rate_limit_rad_s",),
    "wheel_momentum_nms": ("wheel_momentum_nms",),
    "torque_authority": ("torque_authority",),
    "torque_authority_factor": ("torque_authority",),
    # EPS PDU Python SysModel fields.
    "path_resistance_ohm": ("path_resistance_ohm",),
    "bus_voltage_v": ("bus_voltage_v",),
    "channel_current_limit_a": ("channel_current_limit_a",),
    "channel_enabled": ("channel_enabled",),
    "current_limit": ("current_limit",),
    "voltage_regulation": ("voltage_regulation",),
    # Comm/Data direct Basilisk fields. Link/ground-station abstractions without modules remain unsupported.
    "gain_dbi": ("gain_dbi", "nodeBaudRate"),
    "rf_path_factor": ("rf_path_factor", "nodeBaudRate"),
    "pointing_error_deg": ("pointing_error_deg",),
    "margin_db": ("margin_db", "nodeBaudRate"),
    "ber": ("ber",),
    "tracking_error_deg": ("tracking_error_deg",),
    "g_over_t_db": ("g_over_t_db",),
    "weather_loss_db": ("weather_loss_db",),
    "packet_loss_rate": ("packet_loss_rate",),
    "queue_capacity_bits": ("queue_capacity_bits",),
    "bit_error_rate": ("bit_error_rate",),
    # Propulsion fields are only direct if the real module exposes them.
    "isp_s": ("steadyIsp", "isp_s"),
    "leak_rate_kg_s": ("setFuelLeakRate", "leak_rate_kg_s"),
    "tank_pressure_pa": ("tank_pressure_pa",),
    "outlet_flow_factor": ("outlet_flow_factor",),
}


def _get_time_s_from_sim(sim: Any) -> float:
    try:
        from Basilisk.utilities import macros

        return float(sim.TotalSim.CurrentNanos) * macros.NANO2SEC
    except Exception:
        return 0.0


def _sec_to_ns(seconds: float) -> int:
    try:
        from Basilisk.utilities import macros

        return macros.sec2nano(float(seconds))
    except Exception:
        return int(round(float(seconds) * 1.0e9))


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _jsonish(value: Any) -> Any:
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, Mapping):
        return {str(k): _jsonish(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_jsonish(v) for v in value]
    for attr in ("__dict__",):
        if hasattr(value, attr):
            try:
                return {str(k): _jsonish(v) for k, v in vars(value).items() if not str(k).startswith("_")}
            except Exception as exc:
                record_runtime_diagnostic(
                    code='RUNTIME_VALUE_SERIALIZATION_FALLBACK',
                    category=DiagnosticCategory.INTROSPECTION_FALLBACK,
                    location='src/subsystems/runtime_injection.py:_jsonish:01',
                    exception=exc,
                    strict=False,
                )
    return repr(value)


def _effect_to_dict(effect: Any) -> dict[str, Any]:
    return {
        "parameter": str(getattr(effect, "parameter", "")),
        "operation": str(getattr(effect, "operation", "")),
        "magnitude": _safe_float(getattr(effect, "magnitude", 0.0)),
        "description": str(getattr(effect, "description", "")),
    }


def _attr_candidates(effect_parameter: str, mapping_parameter: str) -> tuple[str, ...]:
    candidates: list[str] = []
    for token in [effect_parameter, *str(mapping_parameter).replace("/", " ").split()]:
        if not token:
            continue
        if token in _EFFECT_TO_ATTR:
            candidates.extend(_EFFECT_TO_ATTR[token])
        # Preserve exact names as a final fallback for Python SysModels with
        # matching public attributes.
        candidates.append(token)
    seen: set[str] = set()
    ordered = []
    for item in candidates:
        if item not in seen:
            ordered.append(item)
            seen.add(item)
    return tuple(ordered)


def _get_nested_attr(obj: Any, attr_path: str) -> Any:
    current = obj
    for part in attr_path.split("."):
        current = getattr(current, part)
    return current


def _set_nested_attr(obj: Any, attr_path: str, value: Any) -> bool:
    parts = attr_path.split(".")
    if len(parts) == 1:
        if hasattr(obj, parts[0]):
            setattr(obj, parts[0], value)
            return True
        return False
    parent = obj
    for part in parts[:-1]:
        parent = getattr(parent, part)
    leaf = parts[-1]
    if not hasattr(parent, leaf):
        return False
    try:
        setattr(parent, leaf, value)
        return True
    except Exception:
        # Thermal config dataclasses are frozen; replace the whole cfg object.
        if len(parts) == 2 and parts[0] == "cfg" and hasattr(obj, "cfg"):
            obj.cfg = replace(obj.cfg, **{leaf: value})
            return True
        raise


def _coerce_numeric_like(current: Any, value: float) -> Any:
    """Preserve Basilisk vector/matrix Python shapes when writing scalar effects."""

    if isinstance(current, list):
        return [_coerce_numeric_like(item, value) for item in current]
    if isinstance(current, tuple):
        return tuple(_coerce_numeric_like(item, value) for item in current)
    return float(value)


def _apply_effect_like(current: Any, effect: Any, *, factor: float = 1.0, reverse: bool = False) -> Any:
    value = _apply_effect_value(current, effect, factor=factor, reverse=reverse)
    if isinstance(current, (list, tuple)):
        return _coerce_numeric_like(current, _safe_float(value, 0.0))
    return value


def _apply_effect_value(current: Any, effect: Any, *, factor: float = 1.0, reverse: bool = False) -> Any:
    cur = _safe_float(current, 0.0)
    operation = str(getattr(effect, "operation", ""))
    magnitude = _safe_float(getattr(effect, "magnitude", 0.0))
    severity = _safe_float(getattr(effect, "severity", 1.0), 1.0)
    mag = magnitude * severity * float(factor)
    if reverse:
        # End events restore original snapshots instead of trying to invert every
        # nonlinear operation.  This fallback is only used when no snapshot exists.
        if operation == "add":
            return cur - mag
        if operation == "add_loss":
            return cur + abs(mag)
        return cur
    if operation == "multiply":
        return cur * max(0.0, mag)
    if operation == "add":
        return cur + mag
    if operation == "override":
        return magnitude
    if operation == "clamp_max":
        return min(cur, mag)
    if operation == "clamp_min":
        return max(cur, mag)
    if operation == "add_loss":
        return cur - abs(mag)
    if operation == "degrade_multiply":
        return cur * max(0.0, 1.0 - abs(magnitude) * float(factor))
    if operation == "grow_multiply":
        return cur * (1.0 + abs(magnitude) * float(factor))
    if operation == "grow_add":
        return cur + abs(magnitude) * float(factor)
    return cur


def _module_aliases(context: Any, mapping_module: str) -> tuple[str, ...]:
    subsystem = str(getattr(context, "subsystem", ""))
    modules = getattr(context, "modules", {})
    configured = _KNOWN_MODULE_ALIASES.get(subsystem, {}).get(mapping_module)
    candidates: list[str] = list(configured or ())
    candidates.append(mapping_module)
    # Case-insensitive and tag-based fallback.
    lower_module = mapping_module.lower()
    for key, obj in dict(modules).items():
        key_s = str(key)
        tag = str(getattr(obj, "ModelTag", ""))
        if key_s not in candidates and (lower_module in key_s.lower() or lower_module in tag.lower()):
            candidates.append(key_s)
    seen: set[str] = set()
    out = []
    for key in candidates:
        if key in modules and key not in seen:
            out.append(key)
            seen.add(key)
    return tuple(out)


def _select_targets_for_component(context: Any, spec: Mapping[str, Any]) -> tuple[tuple[str, Any], ...]:
    mapping = dict(spec.get("basilisk_mapping", {}) or {})
    mapping_module = str(mapping.get("module", ""))
    aliases = list(_module_aliases(context, mapping_module))
    subsystem = str(getattr(context, "subsystem", ""))
    component = str(spec.get("component", ""))
    target_id = str(spec.get("target_id", ""))

    # Narrow EPS SimplePowerSink events to the intended load when target IDs are
    # available; otherwise fan out across all load sinks for worst-case scenarios.
    if subsystem == "eps" and mapping_module == "simplePowerSink":
        if "payload" in target_id:
            aliases = ["payload"]
        elif "adcs" in target_id:
            aliases = ["adcs"]
        elif "comm" in target_id:
            aliases = ["comm"]
        elif "heater" in target_id:
            aliases = ["heater"]
        elif component == "power_sink":
            aliases = [key for key in ("payload", "adcs", "comm", "heater", "bus") if key in getattr(context, "modules", {})]

    modules = getattr(context, "modules", {})
    return tuple((key, modules[key]) for key in aliases if key in modules)


_COMMAND_ATTRS = {"thruster_command_scale", "rw_command_scale", "mtb_command_scale"}


def _command_attr_available(context: Any, attr: str) -> bool:
    params = getattr(context, "base_parameters", {})
    handles = getattr(context, "message_handles", {})
    if attr == "thruster_command_scale":
        return bool(params.get("on_time_s")) and handles.get("thruster_command") is not None
    if attr == "rw_command_scale":
        return bool(params.get("rw_motor_torques_nm")) and handles.get("rw_command") is not None
    if attr == "mtb_command_scale":
        return bool(params.get("mtb_dipoles_am2")) and handles.get("mtb_command") is not None
    return False


def _direct_attr_exists(context: Any, module: Any, attr: str) -> bool:
    if attr in _COMMAND_ATTRS:
        return _command_attr_available(context, attr)
    try:
        if attr.startswith("cfg."):
            _get_nested_attr(module, attr)
            return True
        return hasattr(module, attr)
    except Exception:
        return False



_NATIVE_COMPONENT_EFFECTS: Mapping[str, frozenset[str]] = {
    "cmg": frozenset({"friction_torque_nm", "gimbal_rate_limit_rad_s", "wheel_momentum_nms", "torque_authority", "torque_authority_factor"}),
    "thruster": frozenset({"isp_s", "thrust_n", "mass_flow_factor", "valve_opening"}),
    "fuel_tank": frozenset({"leak_rate_kg_s"}),
}

_RUNTIME_OUT_OF_SCOPE_EFFECTS: Mapping[str, frozenset[str]] = {
    # Payload imaging physics was explicitly moved out of the native data-flow scope.
    "payload_sensor": frozenset({"responsivity", "image_quality"}),
    # Storage read/write rates are not SimpleStorageUnit parameters; data producers/consumers own baud rates.
    "data_queue": frozenset({"read_rate_bps", "write_rate_bps"}),
    # Fuel pressure/feed-system proxy quantities are not native FuelTank runtime fields.
    "fuel_tank": frozenset({"tank_pressure_pa", "outlet_flow_factor"}),
    # Current subsystem harness does not instantiate full RF/access native modules for these proxy impairments.
    "ground_station": frozenset({"tracking_error_deg", "weather_loss_db", "g_over_t_db"}),
    "link_budget": frozenset({"margin_db"}),
    "antenna": frozenset({"gain_dbi", "rf_path_factor", "pointing_error_deg"}),
    # The generic ADCS subsystem runner does not instantiate VSCMG for long degradation scenarios; V6 covers CMG native runtime separately.
    "cmg": frozenset({"wheel_momentum_nms"}),
}


def _component_native_effect_supported(context: Any, spec: Mapping[str, Any], effect_parameter: str) -> bool:
    component = str(spec.get("component", ""))
    if effect_parameter not in _NATIVE_COMPONENT_EFFECTS.get(component, frozenset()):
        return False
    if component == "cmg":
        return bool(getattr(context, "modules", {}).get("vscmg_effector") or getattr(context, "modules", {}).get("vscmg_bundle"))
    if component == "thruster":
        bundle = getattr(context, "modules", {}).get("thruster_bundle")
        return bool(getattr(bundle, "thruster_refs", ())) or _command_attr_available(context, "thruster_command_scale")
    if component == "fuel_tank":
        tank = getattr(context, "modules", {}).get("fuel_tank")
        return bool(tank is not None and hasattr(tank, "setFuelLeakRate"))
    return False


def _is_runtime_out_of_scope_spec(spec: Mapping[str, Any]) -> bool:
    component = str(spec.get("component", ""))
    effects = tuple(spec.get("effects", ()) or ())
    if not effects:
        return False
    scoped = _RUNTIME_OUT_OF_SCOPE_EFFECTS.get(component, frozenset())
    return bool(scoped) and all(str(getattr(effect, "parameter", "")) in scoped for effect in effects)


def _out_of_scope_runtime_spec_row(spec: Mapping[str, Any]) -> dict[str, Any]:
    mapping = dict(spec.get("basilisk_mapping", {}) or {})
    effects = tuple(spec.get("effects", ()) or ())
    return {
        "event_name": str(spec.get("event_name", "")),
        "scenario": str(spec.get("scenario", "")),
        "phase": str(spec.get("phase", "update")),
        "component": str(spec.get("component", "")),
        "target_id": str(spec.get("target_id", "")),
        "mapping_module": str(mapping.get("module", "")),
        "mapping_parameter": str(mapping.get("parameter", "")),
        "injection": str(mapping.get("injection", "")),
        "effect_parameters": tuple(str(getattr(effect, "parameter", "")) for effect in effects),
        "disposition": "OUT_OF_SCOPE_RUNTIME_PROXY",
        "reason": "effect belongs to a project proxy or explicitly out-of-current-native-runtime-scope quantity",
    }

def _direct_supported_effects(context: Any, spec: Mapping[str, Any]) -> tuple[Any, ...]:
    """Return only effects that can be applied to a direct Basilisk target."""

    targets = _select_targets_for_component(context, spec)
    if not targets:
        return ()
    mapping = dict(spec.get("basilisk_mapping", {}) or {})
    mapping_parameter = str(mapping.get("parameter", ""))
    supported: list[Any] = []
    for effect in tuple(spec.get("effects", ()) or ()):
        effect_parameter = str(getattr(effect, "parameter", ""))
        for _module_key, module in targets:
            if str(spec.get("component", "")) == "reaction_wheel":
                try:
                    from components.reaction_wheel.degradation import native_runtime_effect_supported

                    if native_runtime_effect_supported(module, effect_parameter):
                        supported.append(effect)
                        break
                except Exception as exc:
                    record_runtime_diagnostic(
                        code='NATIVE_EFFECT_SUPPORT_PROBE_FAILED',
                        category=DiagnosticCategory.RUNTIME_INJECTION_FAILURE,
                        location='src/subsystems/runtime_injection.py:_direct_supported_effects:01',
                        exception=exc,
                        strict=None,
                    )
            if _component_native_effect_supported(context, spec, effect_parameter):
                supported.append(effect)
                break
            if any(_direct_attr_exists(context, module, attr) for attr in _attr_candidates(effect_parameter, mapping_parameter)):
                supported.append(effect)
                break
    return tuple(supported)


def _direct_supported_effect_parameters(context: Any, spec: Mapping[str, Any]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(getattr(effect, "parameter", "")) for effect in _direct_supported_effects(context, spec)))


def _spec_has_direct_target(context: Any, spec: Mapping[str, Any]) -> bool:
    return bool(_direct_supported_effects(context, spec))

def _unsupported_runtime_spec_row(context: Any, spec: Mapping[str, Any]) -> dict[str, Any]:
    mapping = dict(spec.get("basilisk_mapping", {}) or {})
    effects = tuple(spec.get("effects", ()) or ())
    supported_effects = set(_direct_supported_effect_parameters(context, spec))
    return {
        "event_name": str(spec.get("event_name", "")),
        "scenario": str(spec.get("scenario", "")),
        "phase": str(spec.get("phase", "update")),
        "component": str(spec.get("component", "")),
        "target_id": str(spec.get("target_id", "")),
        "mapping_module": str(mapping.get("module", "")),
        "mapping_parameter": str(mapping.get("parameter", "")),
        "injection": str(mapping.get("injection", "")),
        "effect_parameters": tuple(str(getattr(effect, "parameter", "")) for effect in effects),
        "supported_effect_parameters": tuple(sorted(supported_effects)),
        "unsupported_effect_parameters": tuple(
            str(getattr(effect, "parameter", ""))
            for effect in effects
            if str(getattr(effect, "parameter", "")) not in supported_effects
        ),
        "reason": "no direct writable Basilisk module/SysModel/command target in current builder context",
    }


def _with_effect_subset(spec: Mapping[str, Any], effects: Sequence[Any]) -> Mapping[str, Any]:
    copied = dict(spec)
    copied["effects"] = tuple(effects)
    return copied


def partition_direct_runtime_specs(
    context: Any,
    specs: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    """Split specs/effects into direct-supported and unsupported portions.

    A component mechanism can contain multiple low-level effects.  Under the
    direct-only policy, an event may be registered only for the subset of
    effects that map to a writable Basilisk module/SysModel/command target.
    Unsupported effects are retained as metadata/backlog rows and are not
    applied by the runtime event.
    """

    supported: list[Mapping[str, Any]] = []
    unsupported: list[Mapping[str, Any]] = []
    for spec in specs:
        all_effects = tuple(spec.get("effects", ()) or ())
        supported_effects = _direct_supported_effects(context, spec)
        supported_ids = {id(effect) for effect in supported_effects}
        unsupported_effects = tuple(effect for effect in all_effects if id(effect) not in supported_ids)
        if supported_effects:
            supported.append(_with_effect_subset(spec, supported_effects))
        if unsupported_effects or not supported_effects:
            unsupported.append(_with_effect_subset(spec, unsupported_effects))
    return tuple(supported), tuple(unsupported)



def partition_direct_runtime_specs_with_scope(
    context: Any,
    specs: Sequence[Mapping[str, Any]],
) -> tuple[tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...], tuple[Mapping[str, Any], ...]]:
    """Split runtime specs into supported, unsupported, and explicitly out-of-scope portions."""

    supported: list[Mapping[str, Any]] = []
    unsupported: list[Mapping[str, Any]] = []
    out_of_scope: list[Mapping[str, Any]] = []
    for spec in specs:
        all_effects = tuple(spec.get("effects", ()) or ())
        supported_effects = _direct_supported_effects(context, spec)
        supported_ids = {id(effect) for effect in supported_effects}
        unsupported_effects = tuple(effect for effect in all_effects if id(effect) not in supported_ids)
        if supported_effects:
            supported.append(_with_effect_subset(spec, supported_effects))
        if unsupported_effects:
            remainder = _with_effect_subset(spec, unsupported_effects)
            if _is_runtime_out_of_scope_spec(remainder):
                out_of_scope.append(remainder)
            else:
                unsupported.append(remainder)
        elif not supported_effects:
            if _is_runtime_out_of_scope_spec(spec):
                out_of_scope.append(spec)
            else:
                unsupported.append(spec)
    return tuple(supported), tuple(unsupported), tuple(out_of_scope)


def _records(context: Any) -> list[RuntimeInjectionRecord]:
    params = getattr(context, "base_parameters", None)
    if not isinstance(params, dict):
        return []
    return params.setdefault("runtime_injection_records", [])


def _snapshots(context: Any) -> dict[str, dict[str, Any]]:
    params = getattr(context, "base_parameters", None)
    if not isinstance(params, dict):
        return {}
    return params.setdefault("runtime_injection_snapshots", {})


def _write_thruster_command(context: Any, scale: float, *, t_ns: int | None = None) -> bool:
    base = tuple(float(x) for x in getattr(context, "base_parameters", {}).get("on_time_s", ()))
    msg = getattr(context, "message_handles", {}).get("thruster_command")
    if msg is None or not base:
        return False
    try:
        from Basilisk.architecture import messaging

        payload = messaging.THRArrayOnTimeCmdMsgPayload()
        values = list(payload.OnTimeRequest)
        scaled = [max(0.0, x * max(0.0, float(scale))) for x in base]
        for idx, value in enumerate(scaled):
            if idx < len(values):
                values[idx] = float(value)
        payload.OnTimeRequest = values
        if t_ns is None:
            msg.write(payload)
        else:
            msg.write(payload, int(t_ns))
        return True
    except Exception:
        return False


def _write_rw_command(context: Any, scale: float, *, t_ns: int | None = None) -> bool:
    base = tuple(float(x) for x in getattr(context, "base_parameters", {}).get("rw_motor_torques_nm", ()))
    msg = getattr(context, "message_handles", {}).get("rw_command")
    if msg is None or not base:
        return False
    try:
        from Basilisk.architecture import messaging

        payload = messaging.ArrayMotorTorqueMsgPayload()
        values = list(payload.motorTorque)
        scaled = [x * max(0.0, float(scale)) for x in base]
        for idx, value in enumerate(scaled):
            if idx < len(values):
                values[idx] = float(value)
        payload.motorTorque = values
        if t_ns is None:
            msg.write(payload)
        else:
            msg.write(payload, int(t_ns))
        return True
    except Exception:
        return False


def _write_mtb_command(context: Any, scale: float, *, t_ns: int | None = None) -> bool:
    base = tuple(float(x) for x in getattr(context, "base_parameters", {}).get("mtb_dipoles_am2", ()))
    msg = getattr(context, "message_handles", {}).get("mtb_command")
    if msg is None or not base:
        return False
    try:
        from Basilisk.architecture import messaging

        payload = messaging.MTBCmdMsgPayload()
        payload.mtbDipoleCmds = [x * max(0.0, float(scale)) for x in base]
        if t_ns is None:
            msg.write(payload)
        else:
            msg.write(payload, int(t_ns))
        return True
    except Exception:
        return False


def _apply_command_attr(context: Any, attr: str, effect: Any, *, factor: float, reverse: bool, t_ns: int | None) -> tuple[bool, Mapping[str, Any], Mapping[str, Any]]:
    if attr not in {"thruster_command_scale", "rw_command_scale", "mtb_command_scale"}:
        return False, {}, {}
    key = f"command::{attr}"
    snapshots = _snapshots(context)
    base_scale = float(snapshots.setdefault(key, {"scale": 1.0}).get("scale", 1.0))
    if reverse:
        scale = base_scale
    else:
        # Compute scale by applying effect to a nominal authority of 1.0.
        scale = max(0.0, _safe_float(_apply_effect_value(1.0, effect, factor=factor), 1.0))
    if attr == "thruster_command_scale":
        ok = _write_thruster_command(context, scale, t_ns=t_ns)
    elif attr == "rw_command_scale":
        ok = _write_rw_command(context, scale, t_ns=t_ns)
    else:
        ok = _write_mtb_command(context, scale, t_ns=t_ns)
    return ok, {"scale": base_scale}, {"scale": scale}


def _apply_one_effect(
    context: Any,
    module_key: str,
    module: Any,
    spec: Mapping[str, Any],
    effect: Any,
    *,
    factor: float,
    reverse: bool,
    t_ns: int | None
) -> tuple[bool, Mapping[str, Any], Mapping[str, Any], str]:
    mapping = dict(spec.get("basilisk_mapping", {}) or {})
    mapping_parameter = str(mapping.get("parameter", ""))
    effect_parameter = str(getattr(effect, "parameter", ""))
    before: dict[str, Any] = {}
    after: dict[str, Any] = {}
    notes: list[str] = []

    if str(spec.get("component", "")) == "reaction_wheel":
        try:
            from components.reaction_wheel.degradation import apply_native_runtime_effect

            snapshots = _snapshots(context)

            def _snapshot_getter(key: str, current: float) -> float:
                if key not in snapshots:
                    snapshots[key] = {"value": float(current)}
                return float(snapshots[key].get("value", current))

            ok, native_before, native_after, native_note = apply_native_runtime_effect(
                module,
                effect,
                factor=factor,
                reverse=reverse,
                snapshot_prefix=module_key,
                snapshot_getter=_snapshot_getter,
            )
            if ok:
                return True, native_before, native_after, native_note
        except Exception as exc:
            notes.append(f"component_native_rw: {type(exc).__name__}: {exc}")


    component = str(spec.get("component", ""))
    if component == "cmg" and effect_parameter in _NATIVE_COMPONENT_EFFECTS.get("cmg", frozenset()):
        try:
            from components.cmg.degradation import apply_runtime_cmg_degradation
            snapshots = _snapshots(context)
            data = getattr(module, "VSCMGData", None)
            if data is None and hasattr(module, "effector"):
                data = getattr(module.effector, "VSCMGData", None)
            before_cmg: dict[str, Any] = {}
            try:
                for i in range(len(data or [])):
                    unit = data[i]
                    for attr_name in ("wheelLinearFrictionRatio", "gimbalLinearFrictionRatio", "u_s_max", "u_g_max", "gammaDot_max", "Omega", "Omega_max"):
                        if hasattr(unit, attr_name):
                            key = f"{module_key}.VSCMGData[{i}].{attr_name}"
                            before_cmg[key] = _jsonish(getattr(unit, attr_name))
            except Exception as exc:
                record_runtime_diagnostic(
                    code='RUNTIME_EFFECT_EVIDENCE_CAPTURE_FAILED',
                    category=DiagnosticCategory.RUNTIME_INJECTION_FAILURE,
                    location='src/subsystems/runtime_injection.py:_apply_one_effect:01',
                    exception=exc,
                    strict=None,
                )
            friction_growth = 0.0
            torque_loss_fraction = 0.0
            if effect_parameter in {"friction_torque_nm", "gimbal_rate_limit_rad_s"}:
                friction_growth = abs(_safe_float(getattr(effect, "magnitude", 0.0))) * float(factor)
                if effect_parameter == "gimbal_rate_limit_rad_s":
                    torque_loss_fraction = min(1.0, abs(_safe_float(getattr(effect, "magnitude", 0.0))) * float(factor))
            elif effect_parameter in {"wheel_momentum_nms", "torque_authority", "torque_authority_factor"}:
                torque_loss_fraction = min(1.0, abs(_safe_float(getattr(effect, "magnitude", 0.0))) * float(factor))
            mutations = apply_runtime_cmg_degradation(
                module,
                friction_growth=friction_growth,
                torque_loss_fraction=torque_loss_fraction,
            )
            if mutations:
                after_cmg: dict[str, Any] = {}
                try:
                    data2 = getattr(module, "VSCMGData", None)
                    if data2 is None and hasattr(module, "effector"):
                        data2 = getattr(module.effector, "VSCMGData", None)
                    for i in range(len(data2 or [])):
                        unit = data2[i]
                        for attr_name in ("wheelLinearFrictionRatio", "gimbalLinearFrictionRatio", "u_s_max", "u_g_max", "gammaDot_max", "Omega", "Omega_max"):
                            if hasattr(unit, attr_name):
                                key = f"{module_key}.VSCMGData[{i}].{attr_name}"
                                after_cmg[key] = _jsonish(getattr(unit, attr_name))
                except Exception as exc:
                    record_runtime_diagnostic(
                        code='RUNTIME_EFFECT_EVIDENCE_CAPTURE_FAILED',
                        category=DiagnosticCategory.RUNTIME_INJECTION_FAILURE,
                        location='src/subsystems/runtime_injection.py:_apply_one_effect:02',
                        exception=exc,
                        strict=None,
                    )
                return True, before_cmg, after_cmg, "component-native VSCMGData fields updated"
        except Exception as exc:
            notes.append(f"component_native_cmg: {type(exc).__name__}: {exc}")

    if component == "thruster" and effect_parameter in _NATIVE_COMPONENT_EFFECTS.get("thruster", frozenset()):
        refs = tuple(getattr(module, "thruster_refs", ()) or ())
        if refs:
            before_thr: dict[str, Any] = {}
            after_thr: dict[str, Any] = {}
            attr_name = "steadyIsp" if effect_parameter == "isp_s" else "MaxThrust" if effect_parameter == "thrust_n" else "thruster_command_scale"
            if attr_name in _COMMAND_ATTRS:
                command_ok, command_before, command_after = _apply_command_attr(context, attr_name, effect, factor=factor, reverse=reverse, t_ns=t_ns)
                if command_ok:
                    return True, {f"{module_key}.{attr_name}": _jsonish(command_before)}, {f"{module_key}.{attr_name}": _jsonish(command_after)}, "thruster command message rewritten"
            changed = False
            snapshots = _snapshots(context)
            for i, ref in enumerate(refs):
                if not hasattr(ref, attr_name):
                    continue
                key = f"{module_key}.thruster_refs[{i}].{attr_name}"
                current = getattr(ref, attr_name)
                if key not in snapshots:
                    snapshots[key] = {"value": _jsonish(current)}
                before_thr[key] = _jsonish(current)
                new_value = snapshots[key]["value"] if reverse else _apply_effect_like(current, effect, factor=factor)
                setattr(ref, attr_name, new_value)
                after_thr[key] = _jsonish(getattr(ref, attr_name))
                changed = True
            if changed:
                return True, before_thr, after_thr, f"set native thruster {attr_name}"

    if component == "fuel_tank" and effect_parameter == "leak_rate_kg_s":
        tank = module.tank if hasattr(module, "tank") else module
        if hasattr(tank, "setFuelLeakRate"):
            try:
                getter = getattr(tank, "getFuelLeakRate", None)
                before_value = float(getter()) if callable(getter) else 0.0
            except Exception:
                before_value = 0.0
            new_value = before_value if reverse else max(0.0, before_value + abs(_safe_float(getattr(effect, "magnitude", 0.0))) * float(factor))
            tank.setFuelLeakRate(float(new_value))
            # Keep native input message aligned when the builder exposed it.
            try:
                from components.fuel_tank.builder import write_fuel_leak_rate_message
                msg = write_fuel_leak_rate_message(float(new_value))
                tank.fuelLeakRateInMsg.subscribeTo(msg)
                handles = getattr(context, "message_handles", {})
                if isinstance(handles, dict):
                    handles["fuel_leak_rate"] = msg
                if hasattr(module, "leak_rate_msg"):
                    module.leak_rate_msg = msg
            except Exception as msg_exc:
                notes.append(f"fuel_leak_msg: {type(msg_exc).__name__}: {msg_exc}")
            return True, {f"{module_key}.fuelLeakRate": before_value}, {f"{module_key}.fuelLeakRate": new_value}, "native FuelTank leak rate/message updated"

    for attr in _attr_candidates(effect_parameter, mapping_parameter):
        command_ok, command_before, command_after = _apply_command_attr(context, attr, effect, factor=factor, reverse=reverse, t_ns=t_ns)
        if command_ok:
            before.update({f"{module_key}.{attr}": _jsonish(command_before)})
            after.update({f"{module_key}.{attr}": _jsonish(command_after)})
            return True, before, after, "command message rewritten"
        try:
            if attr.startswith("cfg."):
                current = _get_nested_attr(module, attr)
            elif hasattr(module, attr):
                current = getattr(module, attr)
            else:
                continue
            snapshot_key = f"{module_key}.{attr}"
            snapshots = _snapshots(context)
            if snapshot_key not in snapshots:
                snapshots[snapshot_key] = {"value": _jsonish(current)}
            before[snapshot_key] = _jsonish(current)
            if reverse:
                base_value = snapshots.get(snapshot_key, {}).get("value", current)
                new_value = base_value
            else:
                new_value = _apply_effect_like(current, effect, factor=factor)
                # Power sinks use negative values for loads and positive values for sources.
                if attr == "nodePowerOut" and _safe_float(current) < 0.0 and _safe_float(new_value) > 0.0:
                    new_value = -abs(_safe_float(new_value))
                if attr == "nodePowerOut" and _safe_float(current) > 0.0 and _safe_float(new_value) < 0.0:
                    new_value = abs(_safe_float(new_value))
            ok = _set_nested_attr(module, attr, new_value)
            if ok:
                after[snapshot_key] = _jsonish(_get_nested_attr(module, attr) if attr.startswith("cfg.") else getattr(module, attr))
                return True, before, after, f"set {snapshot_key}"
        except Exception as exc:
            notes.append(f"{attr}: {type(exc).__name__}: {exc}")

    note = "unsupported direct target: no writable Basilisk module/SysModel/command field"
    if notes:
        note += "; " + "; ".join(notes[:3])
    return False, before, after, note


class BasiliskDirectRuntimeInjector:
    """Direct runtime injection engine attached to one subsystem Basilisk context."""

    def __init__(self, context: Any):
        self.context = context

    def apply_fault_spec(self, spec: Mapping[str, Any], *, phase: str | None = None, t_ns: int | None = None) -> RuntimeInjectionRecord:
        phase = str(phase or spec.get("phase", "start"))
        reverse = phase == "end"
        return self._apply_spec(spec, phase=phase, factor=1.0, reverse=reverse, t_ns=t_ns)

    def apply_degradation_spec(self, spec: Mapping[str, Any], *, t_s: float, t_ns: int | None = None) -> RuntimeInjectionRecord:
        mechanism = spec.get("degradation") or spec.get("mechanism")
        if mechanism is not None and hasattr(mechanism, "coefficient"):
            factor = _safe_float(mechanism.coefficient(float(t_s)))
        else:
            # Specs generated before this batch may only contain metadata; use a
            # step coefficient after the start time so the event path is still testable.
            factor = 1.0 if float(t_s) >= _safe_float(spec.get("start_s", 0.0)) else 0.0
        return self._apply_spec(spec, phase="update", factor=factor, reverse=False, t_ns=t_ns)

    def _apply_spec(self, spec: Mapping[str, Any], *, phase: str, factor: float, reverse: bool, t_ns: int | None) -> RuntimeInjectionRecord:
        targets = _select_targets_for_component(self.context, spec)
        mapping = dict(spec.get("basilisk_mapping", {}) or {})
        injection = str(mapping.get("injection", ""))
        mapping_module = str(mapping.get("module", ""))
        parameter = str(mapping.get("parameter", ""))
        effects = tuple(spec.get("effects", ()) or ())
        if not effects:
            # A no-effect spec is still recorded for coverage/debugging.
            effects = tuple()

        combined_before: dict[str, Any] = {}
        combined_after: dict[str, Any] = {}
        applied = False
        notes: list[str] = []
        target_list = tuple(targets or ((mapping_module or "<unresolved>", object()),))
        if not effects:
            notes.append("unsupported direct target: spec has no component effects")
        for effect in effects:
            effect_applied = False
            effect_success_notes: list[str] = []
            effect_alias_notes: list[str] = []
            _first_target = target_list[0]
            for module_key, module in target_list:
                ok, before, after, note = _apply_one_effect(
                    self.context,
                    module_key,
                    module,
                    spec,
                    effect,
                    factor=factor,
                    reverse=reverse,
                    t_ns=t_ns
                )
                if ok:
                    effect_applied = True
                    applied = True
                    # Preserve the earliest pre-injection value for each target
                    # while allowing the final post-injection value to reflect
                    # all effects in the same event.  This prevents a later
                    # zero/no-op effect from hiding an earlier real target change.
                    for key, value in before.items():
                        combined_before.setdefault(key, value)
                    combined_after.update(after)
                    effect_success_notes.append(note)
                elif note:
                    effect_alias_notes.append(f"{module_key}: {note}")
            if effect_applied:
                notes.extend(effect_success_notes)
                continue
            effect_name = str(getattr(effect, "parameter", "<unknown>"))
            note = f"unsupported direct effect {effect_name!r}"
            if effect_alias_notes:
                note += "; " + "; ".join(effect_alias_notes[:3])
            notes.append(note)

        sim_time = 0.0
        if t_ns is not None:
            try:
                from Basilisk.utilities import macros

                sim_time = float(t_ns) * macros.NANO2SEC
            except Exception:
                sim_time = float(t_ns) * 1.0e-9
        record = RuntimeInjectionRecord(
            time_s=sim_time,
            event_name=str(spec.get("event_name", "")),
            scenario=str(spec.get("scenario", "")),
            phase=phase,
            component=str(spec.get("component", "")),
            target_id=str(spec.get("target_id", "")),
            injection=injection,
            module_key=mapping_module,
            parameter=parameter,
            applied_to_basilisk=bool(applied),
            before=combined_before,
            after=combined_after,
            effects=tuple(_effect_to_dict(effect) for effect in effects),
            note="; ".join(dict.fromkeys(n for n in notes if n)),
        )
        _records(self.context).append(record)
        return record


def _event_name(raw: str) -> str:
    return "runtime_injection_" + "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in raw)


def attach_fault_runtime_events(context: Any, specs: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Register fault start/end specs as Basilisk runtime events."""

    sim = getattr(context, "simulation", None)
    if sim is None or not hasattr(sim, "createNewEvent"):
        raise RuntimeError("Basilisk simulation context does not expose createNewEvent")
    injector = BasiliskDirectRuntimeInjector(context)
    supported_specs, unsupported_specs, out_of_scope_specs = partition_direct_runtime_specs_with_scope(context, specs)
    params = getattr(context, "base_parameters", {})
    if isinstance(params, dict):
        params["runtime_injection_engine"] = injector
        params["fault_event_specs"] = tuple(params.get("fault_event_specs", ())) + tuple(supported_specs)
        params["unsupported_fault_event_specs"] = tuple(params.get("unsupported_fault_event_specs", ())) + tuple(unsupported_specs)
        params["out_of_scope_fault_event_specs"] = tuple(params.get("out_of_scope_fault_event_specs", ())) + tuple(out_of_scope_specs)
        params["unsupported_runtime_injection_specs"] = tuple(params.get("unsupported_runtime_injection_specs", ())) + tuple(
            _unsupported_runtime_spec_row(context, spec) for spec in unsupported_specs
        )
        params["out_of_scope_runtime_injection_specs"] = tuple(params.get("out_of_scope_runtime_injection_specs", ())) + tuple(
            _out_of_scope_runtime_spec_row(spec) for spec in out_of_scope_specs
        )
    names: list[str] = []
    for idx, spec in enumerate(supported_specs):
        t_ns = _sec_to_ns(_safe_float(spec.get("time_s", 0.0)))
        raw_name = str(spec.get("event_name", f"fault_event_{idx}"))
        event_name = _event_name(raw_name)
        phase = str(spec.get("phase", "start"))

        def _action(parent_sim: Any, *, _spec=spec, _phase=phase) -> None:
            injector.apply_fault_spec(_spec, phase=_phase, t_ns=int(parent_sim.TotalSim.CurrentNanos))

        sim.createNewEvent(
            event_name,
            eventActive=True,
            conditionTime=t_ns,
            actionFunction=_action,
        )
        names.append(event_name)
    if isinstance(params, dict):
        params["registered_fault_runtime_events"] = tuple(params.get("registered_fault_runtime_events", ())) + tuple(names)
    return tuple(names)


def attach_degradation_runtime_events(context: Any, specs: Sequence[Mapping[str, Any]]) -> tuple[str, ...]:
    """Register degradation update specs as periodic Basilisk runtime events."""

    sim = getattr(context, "simulation", None)
    if sim is None or not hasattr(sim, "createNewEvent"):
        raise RuntimeError("Basilisk simulation context does not expose createNewEvent")
    injector = getattr(getattr(context, "base_parameters", {}), "get", lambda *_: None)("runtime_injection_engine") or BasiliskDirectRuntimeInjector(context)
    supported_specs, unsupported_specs, out_of_scope_specs = partition_direct_runtime_specs_with_scope(context, specs)
    params = getattr(context, "base_parameters", {})
    if isinstance(params, dict):
        params["runtime_injection_engine"] = injector
        params["degradation_update_specs"] = tuple(params.get("degradation_update_specs", ())) + tuple(supported_specs)
        params["unsupported_degradation_update_specs"] = tuple(params.get("unsupported_degradation_update_specs", ())) + tuple(unsupported_specs)
        params["out_of_scope_degradation_update_specs"] = tuple(params.get("out_of_scope_degradation_update_specs", ())) + tuple(out_of_scope_specs)
        params["unsupported_runtime_injection_specs"] = tuple(params.get("unsupported_runtime_injection_specs", ())) + tuple(
            _unsupported_runtime_spec_row(context, spec) for spec in unsupported_specs
        )
        params["out_of_scope_runtime_injection_specs"] = tuple(params.get("out_of_scope_runtime_injection_specs", ())) + tuple(
            _out_of_scope_runtime_spec_row(spec) for spec in out_of_scope_specs
        )
    names: list[str] = []
    for idx, spec in enumerate(supported_specs):
        start_ns = _sec_to_ns(_safe_float(spec.get("start_s", 0.0)))
        rate_ns = max(1, _sec_to_ns(_safe_float(spec.get("update_period_s", 10.0), 10.0)))
        raw_name = str(spec.get("event_name", f"degradation_event_{idx}"))
        event_name = _event_name(raw_name)

        def _condition(parent_sim: Any, *, _start_ns=start_ns) -> bool:
            return int(parent_sim.TotalSim.CurrentNanos) >= int(_start_ns)

        def _action(parent_sim: Any, *, _spec=spec, _event_name=event_name) -> None:
            t_ns = int(parent_sim.TotalSim.CurrentNanos)
            injector.apply_degradation_spec(_spec, t_s=_get_time_s_from_sim(parent_sim), t_ns=t_ns)
            # Degradation is periodic, so re-enable after the handler's one-shot
            # deactivation.  ``exactRateMatch=False`` makes the next check occur
            # after ``eventRate`` elapsed time, not necessarily on an exact grid.
            try:
                parent_sim.setEventActivity(_event_name, True)
            except Exception as primary_exc:
                try:
                    parent_sim.eventMap[_event_name].eventActive = True
                except Exception as fallback_exc:
                    raise RuntimeError(
                        f"failed to reactivate periodic degradation event {_event_name!r}: "
                        f"setEventActivity={type(primary_exc).__name__}: {primary_exc}; "
                        f"eventMap fallback={type(fallback_exc).__name__}: {fallback_exc}"
                    ) from fallback_exc

        sim.createNewEvent(
            event_name,
            eventRate=rate_ns,
            eventActive=True,
            conditionFunction=_condition,
            actionFunction=_action,
            exactRateMatch=False,
        )
        names.append(event_name)
    if isinstance(params, dict):
        params["registered_degradation_runtime_events"] = tuple(params.get("registered_degradation_runtime_events", ())) + tuple(names)
    return tuple(names)


def attach_runtime_injection_events(
    context: Any,
    *,
    fault_event_specs: Sequence[Mapping[str, Any]] = (),
    degradation_update_specs: Sequence[Mapping[str, Any]] = (),
) -> Mapping[str, tuple[str, ...]]:
    """Attach fault and degradation runtime events to a subsystem context."""

    return {
        "fault_events": attach_fault_runtime_events(context, fault_event_specs) if fault_event_specs else (),
        "degradation_events": attach_degradation_runtime_events(context, degradation_update_specs) if degradation_update_specs else (),
    }


def runtime_injection_summary(context: Any) -> dict[str, Any]:
    """Return a JSON-friendly summary of registered and applied injections."""

    params = getattr(context, "base_parameters", {})
    if not isinstance(params, dict):
        return {}
    records = tuple(params.get("runtime_injection_records", ()))
    unsupported = tuple(params.get("unsupported_runtime_injection_specs", ()))
    out_of_scope = tuple(params.get("out_of_scope_runtime_injection_specs", ()))
    failed_direct = tuple(r for r in records if not getattr(r, "applied_to_basilisk", False))
    return {
        "policy": "direct_basilisk_targets_only",
        "registered_fault_runtime_events": tuple(params.get("registered_fault_runtime_events", ())),
        "registered_degradation_runtime_events": tuple(params.get("registered_degradation_runtime_events", ())),
        "runtime_injection_records": tuple(
            asdict(record) if isinstance(record, RuntimeInjectionRecord) else record
            for record in records
        ),
        "actual_basilisk_application_count": sum(1 for r in records if getattr(r, "applied_to_basilisk", False)),
        "failed_direct_application_count": len(failed_direct),
        "unsupported_runtime_injection_count": len(unsupported),
        "unsupported_runtime_injection_specs": unsupported,
        "out_of_scope_runtime_injection_count": len(out_of_scope),
        "out_of_scope_runtime_injection_specs": out_of_scope,
    }


def runtime_injection_evidence(
    context: Any,
    *,
    fault_events: Sequence[Mapping[str, Any]] = (),
    degradation_events: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Associate successful native injection with the originating TaskSpec events."""

    evidence = runtime_injection_summary(context)
    applied = int(evidence.get("actual_basilisk_application_count", 0) or 0)
    failed = int(evidence.get("failed_direct_application_count", 0) or 0)
    if applied <= 0 or failed:
        return evidence
    requested_events: list[dict[str, Any]] = []
    for kind, events in (("fault", fault_events), ("degradation", degradation_events)):
        for index, event in enumerate(events):
            event_id = str(
                event.get("id")
                or event.get("modifier_id")
                or event.get(f"{kind}_id")
                or f"{kind}_{index + 1}"
            )
            effect_id = str(
                event.get(f"{kind}_type")
                or event.get("modifier_type")
                or event.get("effect")
                or event.get("type")
                or ""
            )
            requested_events.append({
                "modifier_id": event_id,
                "effect_id": effect_id,
                "kind": kind,
                "runtime_delivery_verified": True,
            })
    evidence["requested_events"] = requested_events
    return evidence


__all__ = [
    "RuntimeInjectionRecord",
    "BasiliskDirectRuntimeInjector",
    "attach_fault_runtime_events",
    "attach_degradation_runtime_events",
    "attach_runtime_injection_events",
    "partition_direct_runtime_specs",
    "partition_direct_runtime_specs_with_scope",
    "runtime_injection_evidence",
    "runtime_injection_summary",
]
