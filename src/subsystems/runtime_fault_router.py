"""Subsystem-owned router for whole-spacecraft runtime fault events.

The whole-spacecraft layer schedules events and records reversible mutations.
This router classifies each event by subsystem, and each subsystem delegates the
actual native/proxy field mapping to its owning component module.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping

from .fault_base import FaultSpec

SetAttr = Callable[[Any, str, Any], None]
ResolveComponent = Callable[[str | None, str], Any | None]
RecordMutation = Callable[[Any, str, Any, Any], None]
RegisterPostRestore = Callable[[Callable[[], None]], None]


@dataclass(frozen=True)
class RuntimeFaultRouteResult:
    handled: bool
    subsystem: str
    fault_value: str
    target_id: str
    mutations: tuple[dict[str, object], ...] = ()
    reason: str = ""


def _fault_value(spec: FaultSpec) -> str:
    return str(getattr(spec.fault_type, "value", spec.fault_type))


def _target(spec: FaultSpec) -> str:
    return str(getattr(spec, "target_id", "") or "")


def classify_runtime_fault(spec: FaultSpec) -> str:
    """Classify a runtime event by target ownership before generic fault name."""

    value = _fault_value(spec)
    target = _target(spec).lower()

    # Target ownership takes precedence for ambiguous names such as
    # ``signal_loss`` and ``power_loss``.
    if any(token in target for token in ("thruster", "fuel_tank")):
        return "propulsion"
    if any(token in target for token in ("payload", "instrument")):
        return "payload"
    if any(token in target for token in ("storage", "transmitter", "antenna", "comm")):
        return "comm_data"
    if any(token in target for token in ("heater", "radiator", "thermal_node")):
        return "thermal"
    if "battery" in target:
        return "eps"
    if target.startswith("rw") or any(token in target for token in ("reaction_wheel", "imu", "star_tracker", "sun_sensor", "magnetometer", "sensor", "cmg", "vscmg", "mtb", "magnetorquer", "magnetic_torquer")):
        return "adcs"

    if value in {"ignition_failure", "nozzle_blockage", "fuel_leak", "rapid_leak", "pressure_loss", "overpressure", "valve_stuck"}:
        return "propulsion"
    if value in {"rw_jamming", "rw_motor_failure", "rw_bearing_seizure", "bias_drift", "noise_increase", "gimbal_motor_failure", "spin_motor_failure", "bearing_wear", "gimbal_lock", "torque_decay", "thermal_overload", "communication_loss", "singularity_lock", "gimbal_stuck", "wheel_drive_fault", "rate_limit_fault", "coil_open", "coil_short", "dipole_saturation", "coil_open_circuit", "coil_short_circuit", "demagnetization", "coil_burnout"}:
        return "adcs"
    if value in {"instrument_off", "stuck_off", "image_degradation", "saturation", "calibration_drift"}:
        return "payload"
    if value in {"capacity_loss", "bad_block", "queue_overflow", "read_only", "link_loss", "signal_loss", "power_loss", "data_corruption", "intermittent", "power_amplifier_failure"}:
        return "comm_data"
    if value in {"thermal_heater_stuck_off", "thermal_heater_stuck_on", "thermal_heater_overheat", "thermal_radiator_rejection_loss", "thermal_node_heat_bias", "heater_failure", "heater_overheating", "heater_stuck"}:
        return "thermal"
    if value in {"sudden_capacity_loss", "open_circuit", "thermal_runaway"}:
        return "eps"
    return "unknown"


def route_runtime_fault(
    spec: FaultSpec,
    component_registry: Mapping[str, Any],
    *,
    set_attr: SetAttr,
    resolve_component: ResolveComponent | None = None,
    record_mutation: RecordMutation | None = None,
    register_post_restore: RegisterPostRestore | None = None,
) -> RuntimeFaultRouteResult:
    """Route one event to a subsystem runtime-fault adapter."""

    subsystem = classify_runtime_fault(spec)
    value = _fault_value(spec)
    target_id = _target(spec)
    common = dict(
        spec=spec,
        component_registry=dict(component_registry),
        set_attr=set_attr,
        resolve_component=resolve_component,
        record_mutation=record_mutation,
        register_post_restore=register_post_restore,
    )
    if subsystem == "adcs":
        from subsystems.adcs.faults import apply_runtime_adcs_fault
        mutations = apply_runtime_adcs_fault(**common)
    elif subsystem == "eps":
        from subsystems.eps.faults import apply_runtime_eps_fault
        mutations = apply_runtime_eps_fault(**common)
    elif subsystem == "propulsion":
        from subsystems.propulsion.faults import apply_runtime_propulsion_fault
        mutations = apply_runtime_propulsion_fault(**common)
    elif subsystem == "payload":
        from subsystems.payload.faults import apply_runtime_payload_fault
        mutations = apply_runtime_payload_fault(**common)
    elif subsystem == "comm_data":
        from subsystems.comm_data.faults import apply_runtime_comm_data_fault
        mutations = apply_runtime_comm_data_fault(**common)
    elif subsystem == "thermal":
        from subsystems.thermal.faults import apply_runtime_thermal_fault
        mutations = apply_runtime_thermal_fault(**common)
    else:
        return RuntimeFaultRouteResult(False, subsystem, value, target_id, reason="no subsystem route")

    mutation_tuple = tuple(mutations or ())
    return RuntimeFaultRouteResult(
        handled=bool(mutation_tuple),
        subsystem=subsystem,
        fault_value=value,
        target_id=target_id,
        mutations=mutation_tuple,
        reason="" if mutation_tuple else "subsystem route found but no component mutation applied",
    )


__all__ = [
    "RuntimeFaultRouteResult",
    "classify_runtime_fault",
    "route_runtime_fault",
]
