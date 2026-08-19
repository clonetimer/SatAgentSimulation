"""Whole-spacecraft degradation aggregation.

The whole-spacecraft layer is a composition layer.  It imports subsystem
``degradation`` modules and asks those modules to build compact degradation
objects.  It must not construct component-level degradation objects directly.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping

from subsystems.adcs import degradation as adcs_degradation_module
from subsystems.adcs.degradation import ADCSDegradation, build_adcs_degradation
from subsystems.comm_data import degradation as comm_data_degradation_module
from subsystems.eps import degradation as eps_degradation_module
from subsystems.eps.degradation import EPSDegradation, build_eps_degradation
from subsystems.payload import degradation as payload_degradation_module
from subsystems.propulsion import degradation as propulsion_degradation_module
from subsystems.propulsion.degradation import PropulsionDegradation, build_propulsion_degradation
from subsystems.thermal import degradation as thermal_degradation_module
from subsystems.thermal.degradation import ThermalDegradation, build_thermal_degradation


SUBSYSTEM_DEGRADATION_MODULES: Mapping[str, Any] = {
    "adcs": adcs_degradation_module,
    "eps": eps_degradation_module,
    "payload": payload_degradation_module,
    "comm_data": comm_data_degradation_module,
    "propulsion": propulsion_degradation_module,
    "thermal": thermal_degradation_module,
}


@dataclass(frozen=True)
class WholeSpacecraftDegradation:
    """Whole-spacecraft degradation state sourced from subsystem factories."""

    eps_degradation: EPSDegradation
    propulsion_degradation: PropulsionDegradation
    adcs_degradation: ADCSDegradation
    thermal_degradation: ThermalDegradation
    payload_degradation: Any | None = None
    comm_data_degradation: Any | None = None
    payload_degradation_scenario: Any | None = None
    comm_data_degradation_scenario: Any | None = None
    source_subsystems: tuple[str, ...] = ("adcs", "eps", "payload", "comm_data", "propulsion", "thermal")

    def total_mission_capability_loss_pct(self) -> float:
        eps_loss = self.eps_degradation.total_power_budget_loss_pct()
        propulsion_loss = self.propulsion_degradation.total_delta_v_loss_pct()
        adcs_loss = _adcs_loss_pct(self.adcs_degradation)
        thermal_loss = self.thermal_degradation.thermal_control_capacity_loss_pct()
        payload_loss = 5.0 if (self.payload_degradation_scenario is not None or self.payload_degradation is not None) else 0.0
        comm_loss = 5.0 if (self.comm_data_degradation_scenario is not None or self.comm_data_degradation is not None) else 0.0
        total_loss = (
            eps_loss * 0.30
            + propulsion_loss * 0.25
            + adcs_loss * 0.18
            + thermal_loss * 0.12
            + payload_loss * 0.08
            + comm_loss * 0.07
        )
        return min(total_loss, 100.0)


# Backward-compatible name used by earlier whole-spacecraft code.
WholeSatelliteDegradation = WholeSpacecraftDegradation


@dataclass(frozen=True)
class WholeSpacecraftDegradationScenario:
    """Scenario metadata preserving the originating subsystem scenario objects."""

    name: str
    degradation: WholeSpacecraftDegradation
    subsystem_scenarios: Mapping[str, Any]
    support: str
    note: str


class DegradationScenario(Enum):
    D1 = "BATTERY_CAPACITY_LOSS_30PCT"
    D2 = "SOLAR_PANEL_EFFICIENCY_LOSS_20PCT"
    D3 = "THRUSTER_THRUST_LOSS_15PCT"
    D4 = "COMBINED_DEGRADATION"
    D5 = "ADCS_RW_FRICTION_AND_SENSOR_NOISE"
    D6 = "THERMAL_HEATER_AND_RADIATOR_DEGRADATION"
    D7 = "FUEL_LEAK_AND_PRESSURE_LOSS"
    D8 = "MULTI_SUBSYSTEM_END_OF_LIFE"


def _subsystem_default_scenarios(subsystem: str) -> Mapping[str, Any]:
    module = SUBSYSTEM_DEGRADATION_MODULES[subsystem]
    default_fn = getattr(module, "default_degradation_scenarios")
    return default_fn()


def _pick_subsystem_scenario(subsystem: str, name: str | None = None) -> Any:
    scenarios = _subsystem_default_scenarios(subsystem)
    if name is None:
        return next(iter(scenarios.values()))
    if name not in scenarios:
        raise KeyError(f"unknown {subsystem} degradation scenario {name!r}; available={tuple(scenarios)}")
    return scenarios[name]


def _zero_degradation() -> WholeSpacecraftDegradation:
    return WholeSpacecraftDegradation(
        eps_degradation=build_eps_degradation(),
        propulsion_degradation=build_propulsion_degradation(),
        adcs_degradation=build_adcs_degradation(),
        thermal_degradation=build_thermal_degradation(),
        payload_degradation=None,
        comm_data_degradation=None,
        payload_degradation_scenario=None,
        comm_data_degradation_scenario=None,
    )


def _adcs_loss_pct(degradation: ADCSDegradation) -> float:
    rw_loss = max(0.0, float(degradation.rw_friction_factor)) * 10.0
    sensor_loss = max(0.0, float(degradation.sensor_noise_factor)) * 10.0
    mtb_loss = max(0.0, 1.0 - float(degradation.mtb_dipole_degradation_factor)) * 100.0
    cmg_loss = max(0.0, 1.0 - float(degradation.cmg_efficiency_factor)) * 100.0
    return min(100.0, 0.35 * rw_loss + 0.35 * sensor_loss + 0.15 * mtb_loss + 0.15 * cmg_loss)


def get_degradation_scenario_config(scenario: DegradationScenario) -> WholeSpacecraftDegradation:
    """Return a whole-spacecraft degradation assembled only through subsystem APIs."""

    base = _zero_degradation()

    if scenario == DegradationScenario.D1:
        return WholeSpacecraftDegradation(
            eps_degradation=build_eps_degradation(battery_capacity_loss_pct=30.0),
            propulsion_degradation=base.propulsion_degradation,
            adcs_degradation=base.adcs_degradation,
            thermal_degradation=base.thermal_degradation,
        )
    if scenario == DegradationScenario.D2:
        return WholeSpacecraftDegradation(
            eps_degradation=build_eps_degradation(solar_efficiency_loss_pct=20.0),
            propulsion_degradation=base.propulsion_degradation,
            adcs_degradation=base.adcs_degradation,
            thermal_degradation=base.thermal_degradation,
        )
    if scenario == DegradationScenario.D3:
        return WholeSpacecraftDegradation(
            eps_degradation=base.eps_degradation,
            propulsion_degradation=build_propulsion_degradation(thrust_loss_pct=15.0),
            adcs_degradation=base.adcs_degradation,
            thermal_degradation=base.thermal_degradation,
        )
    if scenario == DegradationScenario.D4:
        return WholeSpacecraftDegradation(
            eps_degradation=build_eps_degradation(battery_capacity_loss_pct=30.0, solar_efficiency_loss_pct=20.0),
            propulsion_degradation=build_propulsion_degradation(thrust_loss_pct=15.0),
            adcs_degradation=base.adcs_degradation,
            thermal_degradation=base.thermal_degradation,
            payload_degradation=payload_degradation_module.build_payload_degradation_from_scenario(_pick_subsystem_scenario("payload", "instrument_sensor_aging")),
            comm_data_degradation=comm_data_degradation_module.build_comm_data_degradation_from_scenario(_pick_subsystem_scenario("comm_data", "rf_path_aging")),
            payload_degradation_scenario=_pick_subsystem_scenario("payload", "instrument_sensor_aging"),
            comm_data_degradation_scenario=_pick_subsystem_scenario("comm_data", "rf_path_aging"),
        )
    if scenario == DegradationScenario.D5:
        return WholeSpacecraftDegradation(
            eps_degradation=base.eps_degradation,
            propulsion_degradation=base.propulsion_degradation,
            adcs_degradation=build_adcs_degradation(
                rw_friction_factor=5.0,
                sensor_noise_factor=3.0,
                mtb_dipole_degradation_factor=0.8,
                cmg_efficiency_factor=0.85,
            ),
            thermal_degradation=base.thermal_degradation,
        )
    if scenario == DegradationScenario.D6:
        return WholeSpacecraftDegradation(
            eps_degradation=base.eps_degradation,
            propulsion_degradation=base.propulsion_degradation,
            adcs_degradation=base.adcs_degradation,
            thermal_degradation=build_thermal_degradation(
                heater_efficiency_loss_pct=20.0,
                radiator_efficiency_loss_pct=15.0,
                radiator_emissivity_degradation_pct=10.0,
            ),
        )
    if scenario == DegradationScenario.D7:
        return WholeSpacecraftDegradation(
            eps_degradation=base.eps_degradation,
            propulsion_degradation=build_propulsion_degradation(fuel_leak_pct=10.0, pressure_loss_pct=5.0),
            adcs_degradation=base.adcs_degradation,
            thermal_degradation=base.thermal_degradation,
        )
    if scenario == DegradationScenario.D8:
        return WholeSpacecraftDegradation(
            eps_degradation=build_eps_degradation(
                battery_capacity_loss_pct=35.0,
                solar_efficiency_loss_pct=25.0,
                pdu_efficiency_loss_pct=8.0,
            ),
            propulsion_degradation=build_propulsion_degradation(
                thrust_loss_pct=18.0,
                isp_loss_pct=10.0,
                fuel_leak_pct=8.0,
                pressure_loss_pct=6.0,
            ),
            adcs_degradation=build_adcs_degradation(
                rw_friction_factor=4.5,
                sensor_noise_factor=2.5,
                mtb_dipole_degradation_factor=0.82,
                cmg_efficiency_factor=0.88,
            ),
            thermal_degradation=build_thermal_degradation(
                heater_efficiency_loss_pct=15.0,
                radiator_efficiency_loss_pct=12.0,
                radiator_emissivity_degradation_pct=8.0,
            ),
            payload_degradation=payload_degradation_module.build_payload_degradation_from_scenario(_pick_subsystem_scenario("payload", "combined_payload_aging")),
            comm_data_degradation=comm_data_degradation_module.build_comm_data_degradation_from_scenario(_pick_subsystem_scenario("comm_data", "combined_comm_data_aging")),
            payload_degradation_scenario=_pick_subsystem_scenario("payload", "combined_payload_aging"),
            comm_data_degradation_scenario=_pick_subsystem_scenario("comm_data", "combined_comm_data_aging"),
        )
    raise ValueError(f"unknown degradation scenario: {scenario}")


def _scenario_bundle(overrides: Mapping[str, str] | None = None) -> Mapping[str, Any]:
    overrides = dict(overrides or {})
    return {
        subsystem: _pick_subsystem_scenario(subsystem, overrides.get(subsystem))
        for subsystem in SUBSYSTEM_DEGRADATION_MODULES
    }


def default_degradation_scenarios() -> Mapping[str, WholeSpacecraftDegradationScenario]:
    """Return whole-spacecraft scenarios composed from subsystem scenario modules."""

    scenario_overrides: dict[DegradationScenario, Mapping[str, str]] = {
        DegradationScenario.D1: {"eps": "source_storage_aging"},
        DegradationScenario.D2: {"eps": "source_storage_aging"},
        DegradationScenario.D3: {"propulsion": "thruster_aging"},
        DegradationScenario.D4: {"eps": "source_storage_aging", "propulsion": "thruster_aging", "payload": "instrument_sensor_aging", "comm_data": "rf_path_aging"},
        DegradationScenario.D5: {"adcs": "combined_adcs_aging"},
        DegradationScenario.D6: {"thermal": "heater_radiator_aging"},
        DegradationScenario.D7: {"propulsion": "fuel_tank_aging"},
        DegradationScenario.D8: {"adcs": "combined_adcs_aging", "eps": "combined_eps_aging", "payload": "combined_payload_aging", "comm_data": "combined_comm_data_aging", "propulsion": "combined_propulsion_aging", "thermal": "combined_thermal_aging"},
    }

    scenarios: dict[str, WholeSpacecraftDegradationScenario] = {}
    for enum_value in DegradationScenario:
        degradation = get_degradation_scenario_config(enum_value)
        scenarios[enum_value.name.lower()] = WholeSpacecraftDegradationScenario(
            name=enum_value.value,
            degradation=degradation,
            subsystem_scenarios=_scenario_bundle(scenario_overrides.get(enum_value)),
            support="supported_build_time_for_all_subsystems; limited_to_exposed_builder_parameters_for_payload_comm_data",
            note=(
                "Compact runtime degradation fields are built by subsystem degradation factories. "
                "Payload/Comm-Data now expose build-time instrument/transmitter/storage effects; "
                "sensor-quality, BER and RF-margin internals remain metadata until native consumers exist."
            ),
        )
    return scenarios


def degradation_injection_matrix() -> dict[str, dict[str, Any]]:
    """Return degradation-injection support by subsystem, separate from coupling status."""

    return {
        "adcs": {
            "degradation_injection_status": "build_time_supported",
            "evidence": ("subsystems.adcs.degradation.build_adcs_degradation", "build_nominal_adcs_config(cfg.adcs_degradation)"),
            "note": "ADCS degradation changes configuration used when the whole-spacecraft graph is built.",
        },
        "eps": {
            "degradation_injection_status": "build_time_supported",
            "evidence": ("subsystems.eps.degradation.build_eps_degradation", "build_nominal_eps_config(cfg.eps_degradation)"),
            "note": "EPS degradation changes battery/solar/PDU configuration used when the whole-spacecraft graph is built.",
        },
        "propulsion": {
            "degradation_injection_status": "build_time_supported",
            "evidence": ("subsystems.propulsion.degradation.build_propulsion_degradation", "build_nominal_propulsion_config(cfg.propulsion_degradation)"),
            "note": "Propulsion degradation changes thruster/tank configuration used when the graph is built.",
        },
        "thermal": {
            "degradation_injection_status": "build_time_supported",
            "evidence": ("subsystems.thermal.degradation.build_thermal_degradation", "build_nominal_thermal_config(cfg.thermal_degradation)"),
            "note": "Thermal degradation changes heater/radiator/network configuration used when the graph is built.",
        },
        "payload": {
            "degradation_injection_status": "build_time_supported",
            "evidence": (
                "subsystems.payload.degradation.build_payload_degradation_from_scenario",
                "WholeSpacecraftConfig.payload_degradation -> instrument baud/storage capacity factors",
            ),
            "note": "Payload degradation changes whole-spacecraft build parameters for instrument data rate and shared science-storage capacity. Sensor-quality internals remain metadata.",
        },
        "comm_data": {
            "degradation_injection_status": "build_time_supported",
            "evidence": (
                "subsystems.comm_data.degradation.build_comm_data_degradation_from_scenario",
                "WholeSpacecraftConfig.comm_data_degradation -> transmitter baud/storage capacity factors",
            ),
            "note": "Comm/Data degradation changes whole-spacecraft build parameters for transmitter downlink rate and storage capacity. BER/RF-margin internals remain metadata.",
        },
    }


def degradation_source_coverage() -> dict[str, Any]:
    return {
        "required_subsystems": tuple(SUBSYSTEM_DEGRADATION_MODULES.keys()),
        "scenario_counts": {
            subsystem: len(_subsystem_default_scenarios(subsystem))
            for subsystem in SUBSYSTEM_DEGRADATION_MODULES
        },
        "canonical_module": "whole_spacecraft.degradation",
        "uses_subsystem_degradation_modules": True,
        "uses_subsystem_degradation_factories": True,
        "direct_component_degradation_imports": False,
    }


__all__ = [
    "SUBSYSTEM_DEGRADATION_MODULES",
    "WholeSpacecraftDegradation",
    "WholeSatelliteDegradation",
    "WholeSpacecraftDegradationScenario",
    "DegradationScenario",
    "default_degradation_scenarios",
    "get_degradation_scenario_config",
    "degradation_injection_matrix",
    "degradation_source_coverage",
]
