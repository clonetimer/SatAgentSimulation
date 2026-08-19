"""Whole-spacecraft fault aggregation.

The whole-spacecraft layer composes subsystem-level fault scenarios.  Direct
runtime ``FaultSpec`` values are obtained through subsystem fault APIs; this file
must not import component-local fault enums directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from subsystems.adcs import faults as adcs_faults
from subsystems.comm_data import faults as comm_data_faults
from subsystems.eps import faults as eps_faults
from subsystems.payload import faults as payload_faults
from subsystems.propulsion import faults as propulsion_faults
from subsystems.thermal import faults as thermal_faults


SUBSYSTEM_FAULT_MODULES: Mapping[str, Any] = {
    "adcs": adcs_faults,
    "eps": eps_faults,
    "payload": payload_faults,
    "comm_data": comm_data_faults,
    "propulsion": propulsion_faults,
    "thermal": thermal_faults,
}


@dataclass(frozen=True)
class WholeSpacecraftFaultScenario:
    name: str
    subsystem_scenarios: Mapping[str, Any]
    runtime_fault_specs: tuple[Any, ...]
    support: str
    note: str


def _subsystem_default_scenarios(subsystem: str) -> Mapping[str, Any]:
    module = SUBSYSTEM_FAULT_MODULES[subsystem]
    default_fn = getattr(module, "default_fault_scenarios")
    return default_fn()


def _pick_subsystem_scenario(subsystem: str, name: str | None = None) -> Any:
    scenarios = _subsystem_default_scenarios(subsystem)
    if name is None:
        return next(iter(scenarios.values()))
    if name not in scenarios:
        raise KeyError(f"unknown {subsystem} fault scenario {name!r}; available={tuple(scenarios)}")
    return scenarios[name]


def runtime_fault_specs_for_scenario(name: str) -> tuple[Any, ...]:
    """Return direct-only runtime specs through subsystem fault factories."""

    if name == "eps_battery_capacity_loss":
        return eps_faults.build_eps_direct_fault_specs("battery_capacity_loss")
    if name == "eps_battery_open_circuit":
        return eps_faults.build_eps_direct_fault_specs("battery_open_circuit")
    if name == "adcs_rw_jamming":
        return adcs_faults.build_adcs_direct_fault_specs("rw_jamming")
    if name == "adcs_rw_bearing_seizure":
        return adcs_faults.build_adcs_direct_fault_specs("rw_bearing_seizure")
    if name == "propulsion_thruster_ignition_failure":
        return propulsion_faults.build_propulsion_direct_fault_specs("thruster_ignition_failure")
    if name == "payload_instrument_off":
        return payload_faults.build_payload_direct_fault_specs("instrument_off")
    if name == "payload_instrument_degraded_rate":
        return payload_faults.build_payload_direct_fault_specs("instrument_degraded_rate")
    if name == "comm_data_downlink_link_loss":
        return comm_data_faults.build_comm_data_direct_fault_specs("downlink_link_loss")
    if name == "comm_data_storage_capacity_loss":
        return comm_data_faults.build_comm_data_direct_fault_specs("storage_capacity_loss")
    if name == "thermal_heater_stuck_off":
        return thermal_faults.build_thermal_direct_fault_specs("heater_stuck_off")
    if name == "thermal_heater_stuck_on":
        return thermal_faults.build_thermal_direct_fault_specs("heater_stuck_on")
    if name == "thermal_radiator_rejection_loss":
        return thermal_faults.build_thermal_direct_fault_specs("radiator_rejection_loss")
    return ()


def _scenario_bundle(overrides: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    overrides = dict(overrides or {})
    return {
        subsystem: _pick_subsystem_scenario(subsystem, overrides.get(subsystem))
        for subsystem in SUBSYSTEM_FAULT_MODULES
    }


def default_fault_scenarios() -> Mapping[str, WholeSpacecraftFaultScenario]:
    """Return whole-spacecraft scenarios composed from subsystem fault modules."""

    return {
        "eps_battery_capacity_loss": WholeSpacecraftFaultScenario(
            name="eps_battery_capacity_loss",
            subsystem_scenarios=_scenario_bundle({"eps": "battery_capacity_loss"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("eps_battery_capacity_loss"),
            support="supported_static_and_runtime_direct",
            note="Direct runtime specs are built by subsystems.eps.faults.",
        ),
        "adcs_rw_jamming": WholeSpacecraftFaultScenario(
            name="adcs_rw_jamming",
            subsystem_scenarios=_scenario_bundle({"adcs": "rw_bearing_seizure"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("adcs_rw_jamming"),
            support="supported_runtime_native_rw_config_mutation",
            note="The whole-spacecraft layer obtains specs from subsystems.adcs.faults; runtime physics are delegated to components.reaction_wheel.faults.",
        ),
        "adcs_rw_bearing_seizure": WholeSpacecraftFaultScenario(
            name="adcs_rw_bearing_seizure",
            subsystem_scenarios=_scenario_bundle({"adcs": "rw_bearing_seizure"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("adcs_rw_bearing_seizure"),
            support="supported_runtime_native_rw_friction_mutation",
            note="The component layer mutates Basilisk RWConfigPayload friction fields through the ADCS delegation path.",
        ),
        "propulsion_thruster_ignition_failure": WholeSpacecraftFaultScenario(
            name="propulsion_thruster_ignition_failure",
            subsystem_scenarios=_scenario_bundle({"propulsion": "thruster_valve_closed"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("propulsion_thruster_ignition_failure"),
            support="supported_runtime_direct_thruster_config_mutation",
            note="The whole scheduler routes the event to subsystems.propulsion.faults, which delegates THRSimConfig MaxThrust mutation to the thruster component adapter.",
        ),
        "payload_instrument_off": WholeSpacecraftFaultScenario(
            name="payload_instrument_off",
            subsystem_scenarios=_scenario_bundle({"payload": "instrument_off"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("payload_instrument_off"),
            support="supported_runtime_direct_via_mission_gate",
            note="Direct runtime specs are built by subsystems.payload.faults and mutate MissionGate payload nominal rate.",
        ),
        "comm_data_downlink_link_loss": WholeSpacecraftFaultScenario(
            name="comm_data_downlink_link_loss",
            subsystem_scenarios=_scenario_bundle({"comm_data": "transmitter_power_loss"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("comm_data_downlink_link_loss"),
            support="supported_runtime_direct_via_mission_gate",
            note="Direct runtime specs are built by subsystems.comm_data.faults and mutate MissionGate transmitter nominal rate.",
        ),
        "comm_data_storage_capacity_loss": WholeSpacecraftFaultScenario(
            name="comm_data_storage_capacity_loss",
            subsystem_scenarios=_scenario_bundle({"comm_data": "storage_queue_fault"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("comm_data_storage_capacity_loss"),
            support="supported_runtime_direct_module_parameter",
            note="Direct runtime specs are built by subsystems.comm_data.faults and mutate the SimpleStorageUnit capacity field.",
        ),
        "thermal_heater_stuck_off": WholeSpacecraftFaultScenario(
            name="thermal_heater_stuck_off",
            subsystem_scenarios=_scenario_bundle({"thermal": "heater_stuck_off"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("thermal_heater_stuck_off"),
            support="supported_runtime_direct_thermal_heater",
            note="Direct runtime specs are built by subsystems.thermal.faults and mutate registered thermal heater objects.",
        ),
        "thermal_heater_stuck_on": WholeSpacecraftFaultScenario(
            name="thermal_heater_stuck_on",
            subsystem_scenarios=_scenario_bundle({"thermal": "heater_stuck_off"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("thermal_heater_stuck_on"),
            support="supported_runtime_direct_thermal_heater",
            note="Direct runtime specs are built by subsystems.thermal.faults and force registered thermal heater objects on.",
        ),
        "thermal_radiator_rejection_loss": WholeSpacecraftFaultScenario(
            name="thermal_radiator_rejection_loss",
            subsystem_scenarios=_scenario_bundle({"thermal": "radiator_rejection_loss"}),
            runtime_fault_specs=runtime_fault_specs_for_scenario("thermal_radiator_rejection_loss"),
            support="supported_runtime_direct_thermal_radiator",
            note="Direct runtime specs are built by subsystems.thermal.faults and reduce registered radiator rejection factor.",
        ),
        "all_subsystem_metadata": WholeSpacecraftFaultScenario(
            name="all_subsystem_metadata",
            subsystem_scenarios=_scenario_bundle(),
            runtime_fault_specs=(),
            support="metadata_only_for_non_selected_runtime_paths",
            note="Preserves scenario metadata for direct runtime paths that remain outside the current supported set.",
        ),
    }


def fault_event_metadata_for_scenario(name: str) -> tuple[dict[str, Any], ...]:
    scenario = default_fault_scenarios()[name]
    out: list[dict[str, Any]] = []
    for subsystem, subsystem_scenario in scenario.subsystem_scenarios.items():
        module = SUBSYSTEM_FAULT_MODULES[subsystem]
        build_fn = getattr(module, "build_fault_event_specs", None)
        if build_fn is None:
            from subsystems.fault_base import build_fault_event_specs
            build_fn = build_fault_event_specs
        for item in build_fn(subsystem_scenario):
            clean = {k: v for k, v in item.items() if k != "fault"}
            clean["subsystem"] = subsystem
            out.append(clean)
    return tuple(out)


def fault_injection_matrix() -> dict[str, dict[str, Any]]:
    """Return fault-injection support by subsystem, separate from coupling status."""

    return {
        "adcs": {
            "fault_injection_status": "runtime_supported",
            "supported_scenarios": ("adcs_rw_jamming", "adcs_rw_bearing_seizure"),
            "evidence": (
                "whole_spacecraft._runtime_fault_injector delegates to subsystems.adcs.faults.apply_runtime_adcs_fault",
                "subsystems.adcs.faults delegates to components.reaction_wheel.faults.apply_runtime_reaction_wheel_fault",
                "The component mutates Basilisk RWConfigPayload fCoulomb/fStatic/betaStatic/cViscous/u_max fields.",
            ),
            "note": "Basilisk-native RW friction and torque-authority fields are used. The component layer owns the fault mapping; the subsystem and whole-spacecraft layers only compose and route it.",
        },
        "eps": {
            "fault_injection_status": "runtime_supported",
            "supported_scenarios": ("eps_battery_capacity_loss", "eps_battery_open_circuit"),
            "evidence": (
                "subsystems.eps.faults.build_eps_direct_fault_specs",
                "FaultInjector registers the conditionTime event; subsystems.eps routes it to the battery component adapter, which applies the storageCapacity availability hook.",
            ),
            "note": "SimpleBattery exposes no runtime bus-disconnect switch; battery_open_circuit is therefore a capacity/availability proxy hook, not full open-circuit physics.",
        },
        "propulsion": {
            "fault_injection_status": "runtime_supported",
            "supported_scenarios": ("propulsion_thruster_ignition_failure",),
            "evidence": (
                "subsystems.propulsion.faults.build_propulsion_direct_fault_specs",
                "FaultInjector registers the conditionTime event; subsystems.propulsion routes it to the thruster component adapter, which updates THRSimConfig.MaxThrust and refreshes derived properties.",
            ),
            "note": "Runtime ignition-failure support is a direct MaxThrust authority hook against Basilisk THRSimConfig entries; it is not a detailed valve/combustion physics model.",
        },
        "payload": {
            "fault_injection_status": "runtime_supported",
            "supported_scenarios": ("payload_instrument_off", "payload_instrument_degraded_rate"),
            "evidence": (
                "subsystems.payload.faults.build_payload_direct_fault_specs",
                "The scheduler routes through subsystems.payload; the payload component adapter updates the registered mission gate and native instrument rate.",
            ),
            "note": "Payload normal coupling is unchanged; runtime fault support now targets the MissionGate/instrument bridge used by the whole-spacecraft graph.",
        },
        "comm_data": {
            "fault_injection_status": "runtime_supported",
            "supported_scenarios": ("comm_data_downlink_link_loss", "comm_data_storage_capacity_loss"),
            "evidence": (
                "subsystems.comm_data.faults.build_comm_data_direct_fault_specs",
                "The scheduler routes through subsystems.comm_data; transmitter and data-queue component adapters update the registered mission gate/native fields.",
            ),
            "note": "Comm/Data normal coupling is unchanged; runtime fault support now targets MissionGate transmitter rate and storage capacity direct fields.",
        },
        "thermal": {
            "fault_injection_status": "runtime_supported",
            "supported_scenarios": ("thermal_heater_stuck_off", "thermal_heater_stuck_on", "thermal_radiator_rejection_loss"),
            "evidence": (
                "subsystems.thermal.faults.build_thermal_direct_fault_specs",
                "The scheduler routes through subsystems.thermal; heater and radiator component adapters update their registered runtime controls.",
            ),
            "note": "Thermal normal coupling is unchanged; runtime fault support targets builder-registered thermal network or scheduled thermal objects.",
        },
    }


def fault_source_coverage() -> dict[str, Any]:
    return {
        "required_subsystems": tuple(SUBSYSTEM_FAULT_MODULES.keys()),
        "scenario_counts": {
            subsystem: len(_subsystem_default_scenarios(subsystem))
            for subsystem in SUBSYSTEM_FAULT_MODULES
        },
        "canonical_module": "whole_spacecraft.faults",
        "uses_subsystem_fault_modules": True,
        "uses_subsystem_fault_factories": True,
        "direct_component_fault_imports": False,
        "direct_runtime_scenarios": tuple(k for k, v in default_fault_scenarios().items() if v.runtime_fault_specs),
    }


__all__ = [
    "SUBSYSTEM_FAULT_MODULES",
    "WholeSpacecraftFaultScenario",
    "default_fault_scenarios",
    "runtime_fault_specs_for_scenario",
    "fault_event_metadata_for_scenario",
    "fault_injection_matrix",
    "fault_source_coverage",
]
