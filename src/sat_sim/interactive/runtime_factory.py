"""Fail-closed construction of approved persistent interactive runtimes."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from sat_sim.bsk_engine.event_manager import parse_bsk_events
from sat_sim.bsk_engine.unified_native import (
    ADCS_UNIFIED_CAPABILITY_ID,
    WHOLE_UNIFIED_CAPABILITY_ID,
    UnifiedRuntimeConfig,
)

from .models import InteractiveSessionSpec
from .subsystem_runtimes import CommDataPersistentRuntime, EpsPersistentRuntime
from .unified_runtime import UnifiedPersistentRuntime

EPS_CAPABILITY_ID = "subsystem.eps.unified_native.v1"
COMM_DATA_CAPABILITY_ID = "subsystem.comm_data.unified_native.v1"
APPROVED_CAPABILITY_IDS = frozenset({
    WHOLE_UNIFIED_CAPABILITY_ID,
    ADCS_UNIFIED_CAPABILITY_ID,
    EPS_CAPABILITY_ID,
    COMM_DATA_CAPABILITY_ID,
})


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _simulation_number(task_spec: Mapping[str, Any], key: str, default: float) -> float:
    simulation = _mapping(task_spec.get("simulation"))
    solver = _mapping(simulation.get("solver"))
    raw = solver.get(key, default) if key == "step_s" else simulation.get(key, default)
    try:
        return float(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid simulation.{key}") from exc


def _values(task_spec: Mapping[str, Any]) -> dict[str, Any]:
    parameters = _mapping(task_spec.get("parameters"))
    nested = _mapping(parameters.get("values"))
    return nested if nested else parameters


def _has_preplanned_events(task_spec: Mapping[str, Any]) -> bool:
    for section_name in ("events", "modifiers"):
        section = _mapping(task_spec.get(section_name))
        if any(section.get(name) for name in ("faults", "degradations", "constraints")):
            return True
    return False


def build_persistent_runtime(spec: InteractiveSessionSpec):
    """Build one approved runtime without exposing arbitrary constructors."""

    if spec.capability_id not in APPROVED_CAPABILITY_IDS:
        raise ValueError("INTERACTIVE_CAPABILITY_NOT_APPROVED")
    task_spec = spec.task_spec
    duration_s = min(
        max(_simulation_number(task_spec, "duration_s", spec.max_sim_time_s), spec.quantum_s),
        spec.max_sim_time_s,
    )
    step_s = max(_simulation_number(task_spec, "step_s", min(spec.quantum_s, 0.2)), 1e-4)
    if step_s > spec.quantum_s:
        raise ValueError("simulation step_s must not exceed interactive quantum_s")
    values = _values(task_spec)

    if spec.capability_id in {WHOLE_UNIFIED_CAPABILITY_ID, ADCS_UNIFIED_CAPABILITY_ID}:
        config = UnifiedRuntimeConfig(
            capability_id=spec.capability_id,
            duration_s=duration_s,
            step_s=step_s,
            sample_s=max(_simulation_number(task_spec, "sample_s", spec.quantum_s), step_s),
            adcs_only=spec.capability_id == ADCS_UNIFIED_CAPABILITY_ID,
            values=values,
            events=parse_bsk_events(task_spec),
            required_couplings=tuple(
                str(item) for item in (_mapping(task_spec.get("mission")).get("required_couplings") or ())
            ),
            telemetry_streams=tuple(
                dict(item) for item in (_mapping(task_spec.get("outputs")).get("telemetry_streams") or ())
                if isinstance(item, Mapping)
            ),
        )
        return UnifiedPersistentRuntime(config), duration_s

    if _has_preplanned_events(task_spec):
        raise ValueError("SUBSYSTEM_PREPLANNED_EVENTS_NOT_SUPPORTED_INTERACTIVELY")

    if spec.capability_id == EPS_CAPABILITY_ID:
        from subsystems.eps.schemas import EPSBasiliskConfig

        config = EPSBasiliskConfig(
            duration_s=duration_s,
            step_s=step_s,
            battery_capacity_wh=float(values.get("battery_capacity_wh", 160.0)),
            initial_soc=float(values.get("initial_soc", 0.62)),
            solar_power_w=float(values.get("solar_power_w", 95.0)),
            solar_efficiency=float(values.get("solar_efficiency", 0.25)),
            use_simple_solar_panel=bool(values.get("use_simple_solar_panel", False)),
            bus_power_w=float(values.get("bus_power_w", 18.0)),
            payload_power_w=float(values.get("payload_power_w", 38.0)),
            adcs_power_w=float(values.get("adcs_power_w", 20.0)),
            comm_power_w=float(values.get("comm_power_w", 12.0)),
            heater_power_w=float(values.get("heater_power_w", 0.0)),
        )
        return EpsPersistentRuntime(config), duration_s

    from subsystems.comm_data.schemas import CommDataBasiliskConfig

    config = CommDataBasiliskConfig(
        duration_s=duration_s,
        step_s=step_s,
        instrument_baud_bps=float(values.get("instrument_baud_bps", 2.5e6)),
        storage_capacity_bits=float(values.get("storage_capacity_bits", 6.0e9)),
        transmitter_baud_bps=float(values.get("transmitter_baud_bps", 1.5e6)),
        data_name=str(values.get("data_name", "payload_science")),
        initial_storage_bits=float(values.get("initial_storage_bits", 0.0)),
        native_storage_drain_enabled=bool(values.get("native_storage_drain_enabled", True)),
    )
    return CommDataPersistentRuntime(config), duration_s


__all__ = [
    "APPROVED_CAPABILITY_IDS",
    "COMM_DATA_CAPABILITY_ID",
    "EPS_CAPABILITY_ID",
    "build_persistent_runtime",
]
