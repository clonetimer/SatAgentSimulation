"""TaskSpec-to-runtime effect mapping for the composite whole-spacecraft capability.

V37-B keeps ownership boundaries explicit:

* runtime faults are converted to ``FaultSpec`` values and delegated through
  ``whole_spacecraft -> subsystem -> component`` routing;
* degradations are converted to subsystem-owned build-time degradation objects
  before the unified Basilisk graph is built;
* unsupported or time-varying degradation requests are rejected rather than
  post-processing trace rows and presenting that as physical execution.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from typing import Any, Mapping

from components.fault_spec import FaultSpec
from sat_sim.modifiers import ScenarioModifier, normalize_modifiers
from subsystems.adcs.degradation import build_adcs_degradation
from subsystems.comm_data.degradation import build_comm_data_degradation
from subsystems.eps.degradation import build_eps_degradation
from subsystems.payload.degradation import build_payload_degradation
from subsystems.propulsion.degradation import build_propulsion_degradation
from subsystems.thermal.degradation import build_thermal_degradation
from whole_spacecraft.degradation import (
    DegradationScenario,
    WholeSpacecraftDegradation,
    get_degradation_scenario_config,
)
from whole_spacecraft.faults import runtime_fault_specs_for_scenario


FAULT_SCENARIO_ALIASES: dict[str, str] = {
    "eps_battery_capacity_loss": "eps_battery_capacity_loss",
    "battery_capacity_loss": "eps_battery_capacity_loss",
    "capacity_drop": "eps_battery_capacity_loss",
    "capacity_loss": "eps_battery_capacity_loss",
    "sudden_capacity_loss": "eps_battery_capacity_loss",
    "eps_battery_open_circuit": "eps_battery_open_circuit",
    "battery_open_circuit": "eps_battery_open_circuit",
    "open_circuit": "eps_battery_open_circuit",
    "adcs_rw_jamming": "adcs_rw_jamming",
    "rw_jamming": "adcs_rw_jamming",
    "jamming": "adcs_rw_jamming",
    "adcs_rw_bearing_seizure": "adcs_rw_bearing_seizure",
    "rw_bearing_seizure": "adcs_rw_bearing_seizure",
    "bearing_seizure": "adcs_rw_bearing_seizure",
    "propulsion_thruster_ignition_failure": "propulsion_thruster_ignition_failure",
    "thruster_ignition_failure": "propulsion_thruster_ignition_failure",
    "ignition_failure": "propulsion_thruster_ignition_failure",
    "payload_instrument_off": "payload_instrument_off",
    "instrument_off": "payload_instrument_off",
    "stuck_off": "payload_instrument_off",
    "stuck_at_zero": "payload_instrument_off",
    "comm_data_downlink_link_loss": "comm_data_downlink_link_loss",
    "downlink_link_loss": "comm_data_downlink_link_loss",
    "transmitter_outage": "comm_data_downlink_link_loss",
    "link_loss": "comm_data_downlink_link_loss",
    "signal_loss": "comm_data_downlink_link_loss",
    "comm_data_storage_capacity_loss": "comm_data_storage_capacity_loss",
    "storage_capacity_loss": "comm_data_storage_capacity_loss",
    "thermal_heater_stuck_off": "thermal_heater_stuck_off",
    "heater_stuck_off": "thermal_heater_stuck_off",
    "heater_failure": "thermal_heater_stuck_off",
    "heater_stuck": "thermal_heater_stuck_off",
    "thermal_heater_stuck_on": "thermal_heater_stuck_on",
    "heater_stuck_on": "thermal_heater_stuck_on",
    "heater_overheating": "thermal_heater_stuck_on",
    "thermal_radiator_rejection_loss": "thermal_radiator_rejection_loss",
    "radiator_rejection_loss": "thermal_radiator_rejection_loss",
}

DEGRADATION_SCENARIO_ALIASES: dict[str, DegradationScenario] = {
    "d1": DegradationScenario.D1,
    "battery_capacity_loss_30pct": DegradationScenario.D1,
    "battery_capacity_loss": DegradationScenario.D1,
    "d2": DegradationScenario.D2,
    "solar_panel_efficiency_loss_20pct": DegradationScenario.D2,
    "solar_panel_efficiency_loss": DegradationScenario.D2,
    "d3": DegradationScenario.D3,
    "thruster_thrust_loss_15pct": DegradationScenario.D3,
    "thruster_thrust_loss": DegradationScenario.D3,
    "d4": DegradationScenario.D4,
    "combined_degradation": DegradationScenario.D4,
    "d5": DegradationScenario.D5,
    "adcs_rw_friction_and_sensor_noise": DegradationScenario.D5,
    "adcs_end_of_life": DegradationScenario.D5,
    "d6": DegradationScenario.D6,
    "thermal_heater_and_radiator_degradation": DegradationScenario.D6,
    "thermal_end_of_life": DegradationScenario.D6,
    "d7": DegradationScenario.D7,
    "fuel_leak_and_pressure_loss": DegradationScenario.D7,
    "propulsion_end_of_life": DegradationScenario.D7,
    "d8": DegradationScenario.D8,
    "multi_subsystem_end_of_life": DegradationScenario.D8,
    "whole_spacecraft_end_of_life": DegradationScenario.D8,
}


@dataclass(frozen=True)
class EffectResolutionIssue:
    severity: str
    path: str
    message: str
    code: str


@dataclass(frozen=True)
class ResolvedWholeSpacecraftEffects:
    requested_mode: str
    effective_mode: str
    fault_specs: tuple[FaultSpec, ...]
    degradation: WholeSpacecraftDegradation | None
    effects: tuple[dict[str, Any], ...]
    issues: tuple[EffectResolutionIssue, ...]

    @property
    def errors(self) -> tuple[EffectResolutionIssue, ...]:
        return tuple(item for item in self.issues if item.severity == "error")

    @property
    def warnings(self) -> tuple[EffectResolutionIssue, ...]:
        return tuple(item for item in self.issues if item.severity == "warning")


@dataclass
class _DegradationAccumulator:
    battery_capacity_loss_pct: float = 0.0
    solar_efficiency_loss_pct: float = 0.0
    pdu_efficiency_loss_pct: float = 0.0
    rw_friction_factor: float = 0.0
    sensor_noise_factor: float = 0.0
    mtb_dipole_degradation_factor: float = 1.0
    cmg_efficiency_factor: float = 1.0
    thrust_loss_pct: float = 0.0
    isp_loss_pct: float = 0.0
    fuel_leak_pct: float = 0.0
    pressure_loss_pct: float = 0.0
    heater_efficiency_loss_pct: float = 0.0
    radiator_efficiency_loss_pct: float = 0.0
    radiator_emissivity_degradation_pct: float = 0.0
    payload_instrument_baud_factor: float = 1.0
    payload_storage_capacity_factor: float = 1.0
    comm_transmitter_baud_factor: float = 1.0
    comm_storage_capacity_factor: float = 1.0
    has_effect: bool = False

    def merge_scenario(self, degradation: WholeSpacecraftDegradation) -> None:
        eps = degradation.eps_degradation
        adcs = degradation.adcs_degradation
        propulsion = degradation.propulsion_degradation
        thermal = degradation.thermal_degradation
        self.battery_capacity_loss_pct = max(
            self.battery_capacity_loss_pct,
            float(eps.battery_degradation.capacity_loss_pct),
        )
        self.solar_efficiency_loss_pct = max(
            self.solar_efficiency_loss_pct,
            float(eps.solar_panel_degradation.efficiency_loss_pct),
        )
        self.pdu_efficiency_loss_pct = max(
            self.pdu_efficiency_loss_pct,
            float(eps.pdu_efficiency_loss_pct),
        )
        self.rw_friction_factor = max(self.rw_friction_factor, float(adcs.rw_friction_factor))
        self.sensor_noise_factor = max(self.sensor_noise_factor, float(adcs.sensor_noise_factor))
        self.mtb_dipole_degradation_factor = min(
            self.mtb_dipole_degradation_factor,
            float(adcs.mtb_dipole_degradation_factor),
        )
        self.cmg_efficiency_factor = min(
            self.cmg_efficiency_factor,
            float(adcs.cmg_efficiency_factor),
        )
        self.thrust_loss_pct = max(
            self.thrust_loss_pct,
            float(propulsion.thruster_degradation.thrust_loss_pct),
        )
        self.isp_loss_pct = max(
            self.isp_loss_pct,
            float(propulsion.thruster_degradation.isp_loss_pct),
        )
        self.fuel_leak_pct = max(
            self.fuel_leak_pct,
            float(propulsion.fuel_tank_degradation.fuel_leak_pct),
        )
        self.pressure_loss_pct = max(
            self.pressure_loss_pct,
            float(propulsion.fuel_tank_degradation.pressure_loss_pct),
        )
        self.heater_efficiency_loss_pct = max(
            self.heater_efficiency_loss_pct,
            float(thermal.heater_degradation.efficiency_loss_pct),
        )
        self.radiator_efficiency_loss_pct = max(
            self.radiator_efficiency_loss_pct,
            float(thermal.radiator_degradation.efficiency_loss_pct),
        )
        self.radiator_emissivity_degradation_pct = max(
            self.radiator_emissivity_degradation_pct,
            float(thermal.radiator_degradation.emissivity_degradation_pct),
        )
        payload = degradation.payload_degradation
        if payload is not None:
            self.payload_instrument_baud_factor = min(
                self.payload_instrument_baud_factor,
                float(getattr(payload, "instrument_baud_factor", 1.0)),
            )
            self.payload_storage_capacity_factor = min(
                self.payload_storage_capacity_factor,
                float(getattr(payload, "storage_capacity_factor", 1.0)),
            )
        comm = degradation.comm_data_degradation
        if comm is not None:
            self.comm_transmitter_baud_factor = min(
                self.comm_transmitter_baud_factor,
                float(getattr(comm, "transmitter_baud_factor", 1.0)),
            )
            self.comm_storage_capacity_factor = min(
                self.comm_storage_capacity_factor,
                float(getattr(comm, "storage_capacity_factor", 1.0)),
            )
        self.has_effect = True

    def build(self) -> WholeSpacecraftDegradation | None:
        if not self.has_effect:
            return None
        return WholeSpacecraftDegradation(
            eps_degradation=build_eps_degradation(
                battery_capacity_loss_pct=self.battery_capacity_loss_pct,
                solar_efficiency_loss_pct=self.solar_efficiency_loss_pct,
                pdu_efficiency_loss_pct=self.pdu_efficiency_loss_pct,
            ),
            propulsion_degradation=build_propulsion_degradation(
                thrust_loss_pct=self.thrust_loss_pct,
                isp_loss_pct=self.isp_loss_pct,
                fuel_leak_pct=self.fuel_leak_pct,
                pressure_loss_pct=self.pressure_loss_pct,
            ),
            adcs_degradation=build_adcs_degradation(
                rw_friction_factor=self.rw_friction_factor,
                sensor_noise_factor=self.sensor_noise_factor,
                mtb_dipole_degradation_factor=self.mtb_dipole_degradation_factor,
                cmg_efficiency_factor=self.cmg_efficiency_factor,
            ),
            thermal_degradation=build_thermal_degradation(
                heater_efficiency_loss_pct=self.heater_efficiency_loss_pct,
                radiator_efficiency_loss_pct=self.radiator_efficiency_loss_pct,
                radiator_emissivity_degradation_pct=self.radiator_emissivity_degradation_pct,
            ),
            payload_degradation=build_payload_degradation(
                instrument_baud_factor=self.payload_instrument_baud_factor,
                storage_capacity_factor=self.payload_storage_capacity_factor,
                source_scenario="taskspec_v37b",
            ),
            comm_data_degradation=build_comm_data_degradation(
                transmitter_baud_factor=self.comm_transmitter_baud_factor,
                storage_capacity_factor=self.comm_storage_capacity_factor,
                source_scenario="taskspec_v37b",
            ),
        )


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _canonical_text(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_").replace(" ", "_")


def _explicit_scenario(modifier: ScenarioModifier) -> str:
    params = _mapping(modifier.parameters)
    value = params.get("scenario") or params.get("scenario_id") or params.get("whole_spacecraft_scenario")
    return _canonical_text(value)


def _loss_pct(modifier: ScenarioModifier, *parameter_names: str, default_pct: float = 10.0) -> float:
    params = _mapping(modifier.parameters)
    for name in parameter_names:
        value = params.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, min(100.0, float(value)))
    if modifier.scale is not None:
        return max(0.0, min(100.0, (1.0 - float(modifier.scale)) * 100.0))
    if modifier.severity is not None:
        return max(0.0, min(100.0, float(modifier.severity) * 100.0))
    return float(default_pct)


def _factor(modifier: ScenarioModifier, *parameter_names: str, default_factor: float = 0.8) -> float:
    params = _mapping(modifier.parameters)
    for name in parameter_names:
        value = params.get(name)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return max(0.0, float(value))
    if modifier.scale is not None:
        return max(0.0, float(modifier.scale))
    if modifier.severity is not None:
        return max(0.0, 1.0 - float(modifier.severity))
    return float(default_factor)


def _fault_scenario_for(modifier: ScenarioModifier) -> str | None:
    explicit = _explicit_scenario(modifier)
    if explicit:
        return FAULT_SCENARIO_ALIASES.get(explicit)
    kind = _canonical_text(modifier.modifier_type)
    target = _canonical_text(modifier.target).replace("_", ".")
    direct = FAULT_SCENARIO_ALIASES.get(kind)
    if direct:
        if kind == "capacity_loss" and "storage" in target:
            return "comm_data_storage_capacity_loss"
        return direct
    if target == "eps.battery" and kind in {"capacity_drop", "capacity_loss", "sudden_capacity_loss"}:
        return "eps_battery_capacity_loss"
    if target == "eps.battery" and kind == "open_circuit":
        return "eps_battery_open_circuit"
    if target == "adcs.reaction.wheel" and kind in {"rw_jamming", "jamming", "torque_limit_drop"}:
        return "adcs_rw_jamming"
    if target == "adcs.reaction.wheel" and kind in {"rw_bearing_seizure", "bearing_seizure"}:
        return "adcs_rw_bearing_seizure"
    if target == "propulsion.thruster" and kind == "ignition_failure":
        return "propulsion_thruster_ignition_failure"
    if target in {"payload.instrument", "payload"} and kind in {"instrument_off", "stuck_off", "stuck_at_zero"}:
        return "payload_instrument_off"
    if target in {"comm.transmitter", "communication.transmitter"} and kind in {"transmitter_outage", "link_loss", "signal_loss"}:
        return "comm_data_downlink_link_loss"
    if target in {"comm.storage", "comm.data.storage", "payload.storage"} and kind in {"capacity_loss", "storage_capacity_loss"}:
        return "comm_data_storage_capacity_loss"
    if target == "thermal.heater" and kind in {"heater_stuck", "heater_failure", "heater_stuck_off"}:
        return "thermal_heater_stuck_off"
    if target == "thermal.heater" and kind in {"heater_stuck_on", "heater_overheating"}:
        return "thermal_heater_stuck_on"
    if target == "thermal.radiator" and kind in {"radiator_rejection_loss", "efficiency_drop"}:
        return "thermal_radiator_rejection_loss"
    return None


def _clone_fault_specs(modifier: ScenarioModifier, scenario: str) -> tuple[FaultSpec, ...]:
    baseline = runtime_fault_specs_for_scenario(scenario)
    if not baseline:
        return ()
    params = _mapping(modifier.parameters)
    magnitude_raw = params.get("magnitude")
    if isinstance(magnitude_raw, (int, float)) and not isinstance(magnitude_raw, bool):
        magnitude = float(magnitude_raw)
    elif modifier.severity is not None:
        magnitude = float(modifier.severity)
    elif modifier.scale is not None:
        magnitude = max(0.0, 1.0 - float(modifier.scale))
    else:
        magnitude = None
    target_override = params.get("target_id")
    out: list[FaultSpec] = []
    for item in baseline:
        out.append(
            replace(
                item,
                onset_time_s=float(modifier.onset_time_s),
                duration_s=float(modifier.duration_s),
                magnitude=float(item.magnitude if magnitude is None else magnitude),
                target_id=str(target_override) if target_override else item.target_id,
            )
        )
    return tuple(out)


def _apply_degradation_modifier(
    accumulator: _DegradationAccumulator,
    modifier: ScenarioModifier,
) -> tuple[bool, str]:
    explicit = _explicit_scenario(modifier)
    kind = _canonical_text(modifier.modifier_type)
    if explicit:
        scenario = DEGRADATION_SCENARIO_ALIASES.get(explicit)
        if scenario is None:
            return False, f"unknown whole-spacecraft degradation scenario {explicit!r}"
        accumulator.merge_scenario(get_degradation_scenario_config(scenario))
        return True, scenario.name.lower()
    direct_scenario = DEGRADATION_SCENARIO_ALIASES.get(kind)
    if direct_scenario is not None:
        accumulator.merge_scenario(get_degradation_scenario_config(direct_scenario))
        return True, direct_scenario.name.lower()

    target = _canonical_text(modifier.target).replace("_", ".")
    if target == "eps.battery" and kind in {"capacity_fade", "capacity_loss", "efficiency_scale"}:
        accumulator.battery_capacity_loss_pct = max(
            accumulator.battery_capacity_loss_pct,
            _loss_pct(modifier, "capacity_loss_pct"),
        )
    elif target == "eps.solar.panel" and kind in {"efficiency_scale", "efficiency_loss", "solar_panel_efficiency_loss"}:
        accumulator.solar_efficiency_loss_pct = max(
            accumulator.solar_efficiency_loss_pct,
            _loss_pct(modifier, "efficiency_loss_pct"),
        )
    elif target == "eps.pdu" and kind in {"efficiency_scale", "efficiency_loss"}:
        accumulator.pdu_efficiency_loss_pct = max(
            accumulator.pdu_efficiency_loss_pct,
            _loss_pct(modifier, "efficiency_loss_pct"),
        )
    elif target == "adcs.reaction.wheel" and kind in {"friction_increase", "bearing_wear", "rw_friction"}:
        params = _mapping(modifier.parameters)
        value = params.get("rw_friction_factor")
        friction = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else max(0.0, (_loss_pct(modifier) / 100.0) * 5.0)
        accumulator.rw_friction_factor = max(accumulator.rw_friction_factor, friction)
    elif target in {"adcs.sensor", "adcs.sensors"} and kind in {"noise_increase", "sensor_noise", "sensor_noise_increase"}:
        params = _mapping(modifier.parameters)
        value = params.get("sensor_noise_factor")
        noise = float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else max(0.0, (_loss_pct(modifier) / 100.0) * 3.0)
        accumulator.sensor_noise_factor = max(accumulator.sensor_noise_factor, noise)
    elif target == "propulsion.thruster" and kind in {"thrust_loss", "thrust_scale", "efficiency_scale"}:
        accumulator.thrust_loss_pct = max(accumulator.thrust_loss_pct, _loss_pct(modifier, "thrust_loss_pct"))
    elif target == "propulsion.thruster" and kind in {"isp_loss", "specific_impulse_loss"}:
        accumulator.isp_loss_pct = max(accumulator.isp_loss_pct, _loss_pct(modifier, "isp_loss_pct"))
    elif target == "propulsion.fuel.tank" and kind in {"fuel_leak", "fuel_loss"}:
        accumulator.fuel_leak_pct = max(accumulator.fuel_leak_pct, _loss_pct(modifier, "fuel_leak_pct"))
    elif target == "propulsion.fuel.tank" and kind in {"pressure_loss", "pressure_scale"}:
        accumulator.pressure_loss_pct = max(accumulator.pressure_loss_pct, _loss_pct(modifier, "pressure_loss_pct"))
    elif target == "thermal.heater" and kind in {"heater_efficiency_scale", "efficiency_scale", "efficiency_loss"}:
        accumulator.heater_efficiency_loss_pct = max(
            accumulator.heater_efficiency_loss_pct,
            _loss_pct(modifier, "efficiency_loss_pct"),
        )
    elif target == "thermal.radiator" and kind in {"efficiency_scale", "efficiency_loss", "radiator_efficiency_loss"}:
        accumulator.radiator_efficiency_loss_pct = max(
            accumulator.radiator_efficiency_loss_pct,
            _loss_pct(modifier, "efficiency_loss_pct"),
        )
    elif target == "thermal.radiator" and kind in {"emissivity_loss", "emissivity_degradation"}:
        accumulator.radiator_emissivity_degradation_pct = max(
            accumulator.radiator_emissivity_degradation_pct,
            _loss_pct(modifier, "emissivity_degradation_pct"),
        )
    elif target in {"payload.instrument", "payload"} and kind in {"data_rate_scale", "instrument_baud_scale", "efficiency_scale"}:
        accumulator.payload_instrument_baud_factor = min(
            accumulator.payload_instrument_baud_factor,
            _factor(modifier, "instrument_baud_factor"),
        )
    elif target == "payload.storage" and kind in {"capacity_fade", "capacity_scale", "efficiency_scale"}:
        accumulator.payload_storage_capacity_factor = min(
            accumulator.payload_storage_capacity_factor,
            _factor(modifier, "storage_capacity_factor"),
        )
    elif target in {"comm.transmitter", "communication.transmitter"} and kind in {"downlink_scale", "transmitter_baud_scale", "efficiency_scale"}:
        accumulator.comm_transmitter_baud_factor = min(
            accumulator.comm_transmitter_baud_factor,
            _factor(modifier, "transmitter_baud_factor"),
        )
    elif target in {"comm.storage", "comm.data.storage"} and kind in {"capacity_fade", "capacity_scale", "efficiency_scale"}:
        accumulator.comm_storage_capacity_factor = min(
            accumulator.comm_storage_capacity_factor,
            _factor(modifier, "storage_capacity_factor"),
        )
    else:
        return False, f"unsupported degradation mapping target={modifier.target!r}, type={modifier.modifier_type!r}"
    accumulator.has_effect = True
    return True, kind


def _effective_mode(fault_count: int, degradation_count: int) -> str:
    if fault_count and degradation_count:
        return "mixed"
    if fault_count:
        return "fault"
    if degradation_count:
        return "degradation"
    return "nominal"


def resolve_whole_spacecraft_effects(spec: Mapping[str, Any]) -> ResolvedWholeSpacecraftEffects:
    modifiers = normalize_modifiers(spec)
    issues: list[EffectResolutionIssue] = []
    effects: list[dict[str, Any]] = []
    fault_specs: list[FaultSpec] = []
    accumulator = _DegradationAccumulator()
    degradation_count = 0

    for index, modifier in enumerate(modifiers):
        path = f"$.modifiers.{modifier.kind}s[{index}]"
        if modifier.kind == "fault":
            scenario = _fault_scenario_for(modifier)
            if scenario is None:
                issues.append(EffectResolutionIssue(
                    "error",
                    path,
                    f"unsupported whole-spacecraft runtime fault mapping target={modifier.target!r}, type={modifier.modifier_type!r}",
                    "WHOLE_SPACECRAFT_FAULT_UNSUPPORTED",
                ))
                continue
            specs = _clone_fault_specs(modifier, scenario)
            if not specs:
                issues.append(EffectResolutionIssue(
                    "error",
                    path,
                    f"fault scenario {scenario!r} has no runtime FaultSpec factory",
                    "WHOLE_SPACECRAFT_FAULT_FACTORY_EMPTY",
                ))
                continue
            fault_specs.extend(specs)
            effects.append({
                "effect_id": modifier.modifier_id,
                "kind": "fault",
                "scenario": scenario,
                "target": modifier.target,
                "requested_type": modifier.modifier_type,
                "onset_time_s": modifier.onset_time_s,
                "duration_s": modifier.duration_s,
                "runtime_route": "whole_spacecraft->subsystem->component",
                "fault_specs": [
                    {
                        **asdict(item),
                        "fault_type": str(getattr(item.fault_type, "value", item.fault_type)),
                    }
                    for item in specs
                ],
            })
            continue

        if modifier.onset_time_s != 0.0 or modifier.duration_s != -1.0:
            issues.append(EffectResolutionIssue(
                "error",
                path,
                "whole-spacecraft degradation is build-time only in V37-B; onset_time_s must be 0 and duration_s must be -1",
                "WHOLE_SPACECRAFT_DEGRADATION_BUILD_TIME_ONLY",
            ))
            continue
        applied, detail = _apply_degradation_modifier(accumulator, modifier)
        if not applied:
            issues.append(EffectResolutionIssue(
                "error",
                path,
                detail,
                "WHOLE_SPACECRAFT_DEGRADATION_UNSUPPORTED",
            ))
            continue
        degradation_count += 1
        effects.append({
            "effect_id": modifier.modifier_id,
            "kind": "degradation",
            "scenario": detail,
            "target": modifier.target,
            "requested_type": modifier.modifier_type,
            "onset_time_s": 0.0,
            "duration_s": -1.0,
            "runtime_route": "taskspec->subsystem degradation factory->whole_spacecraft build",
        })

    effective_mode = _effective_mode(len(fault_specs), degradation_count)
    target = _mapping(spec.get("target"))
    requested_mode = str(target.get("mode") or effective_mode)
    if requested_mode not in {"nominal", "fault", "degradation", "mixed"}:
        issues.append(EffectResolutionIssue(
            "error",
            "$.target.mode",
            f"unsupported composite whole-spacecraft mode {requested_mode!r}",
            "WHOLE_SPACECRAFT_MODE_UNSUPPORTED",
        ))
    elif requested_mode != effective_mode:
        issues.append(EffectResolutionIssue(
            "error",
            "$.target.mode",
            f"target.mode={requested_mode!r} does not match mapped effects ({effective_mode!r})",
            "WHOLE_SPACECRAFT_MODE_EFFECT_MISMATCH",
        ))

    return ResolvedWholeSpacecraftEffects(
        requested_mode=requested_mode,
        effective_mode=effective_mode,
        fault_specs=tuple(fault_specs),
        degradation=accumulator.build(),
        effects=tuple(effects),
        issues=tuple(issues),
    )


def supported_whole_spacecraft_effect_catalog() -> dict[str, Any]:
    return {
        "fault_scenarios": tuple(sorted(set(FAULT_SCENARIO_ALIASES.values()))),
        "degradation_scenarios": {
            key: value.value for key, value in sorted(DEGRADATION_SCENARIO_ALIASES.items())
        },
        "fault_execution": "runtime_basilisk_event_subsystem_component_route",
        "degradation_execution": "build_time_subsystem_configuration",
        "timed_degradation_supported": False,
        "mixed_mode_supported": True,
    }


__all__ = [
    "EffectResolutionIssue",
    "ResolvedWholeSpacecraftEffects",
    "resolve_whole_spacecraft_effects",
    "supported_whole_spacecraft_effect_catalog",
]
