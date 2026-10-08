"""HF-5 power/thermal/orbit coupled spacecraft model.

This Route-B model uses the HF-1/HF-2 foundations and the HF-3 medium orbit
environment model to drive a deterministic EPS + lumped thermal coupling.  It is
intended as an auditable engineering prototype, not a full high-fidelity thermal
or EPS validation model.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable, Mapping
import math

from sat_sim.orbit import MediumOrbitConfig, propagate_medium_orbit_environment, build_hf3_orbit_environment_payload
from sat_sim.orbit.numerical import OrbitFidelityConfig, propagate_orbit_fidelity, build_orb1_orbit_fidelity_payload
from sat_sim.time_systems import build_time_grid

HF5_POWER_THERMAL_ORBIT_SCHEMA_VERSION = "hf5.power_thermal_orbit_coupled.v1"


class PowerThermalOrbitCoupledError(ValueError):
    """Raised when HF-5 coupled model inputs are invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise PowerThermalOrbitCoupledError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out <= 0.0:
        raise PowerThermalOrbitCoupledError(f"{name} must be positive")
    return out


def _nonnegative(value: Any, name: str) -> float:
    out = _finite(value, name)
    if out < 0.0:
        raise PowerThermalOrbitCoupledError(f"{name} must be non-negative")
    return out


def _ratio(value: Any, name: str) -> float:
    out = _finite(value, name)
    if not 0.0 <= out <= 1.0:
        raise PowerThermalOrbitCoupledError(f"{name} must be in [0, 1]")
    return out


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*mappings: Mapping[str, Any], key: str, default: Any) -> Any:
    for mapping in mappings:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return default


@dataclass(frozen=True)
class PowerThermalOrbitCoupledConfig:
    """Configuration for the HF-5 coupled model."""

    duration_s: float = 5400.0
    sample_s: float = 60.0
    epoch_utc: str = "2026-07-05T00:00:00Z"
    battery_capacity_wh: float = 180.0
    initial_soc: float = 0.72
    solar_array_max_power_w: float = 150.0
    bus_load_power_w: float = 24.0
    payload_load_power_w: float = 32.0
    adcs_load_power_w: float = 8.0
    comm_load_power_w: float = 6.0
    eps_efficiency: float = 0.97
    electrical_load_heat_fraction: float = 0.88
    solar_heat_w: float = 50.0
    initial_bus_temp_c: float = 18.0
    initial_battery_temp_c: float = 16.0
    bus_thermal_capacity_j_k: float = 18_000.0
    battery_thermal_capacity_j_k: float = 9_000.0
    radiator_area_m2: float = 0.35
    radiator_emissivity: float = 0.82
    sink_temp_c: float = -35.0
    thermal_conductance_bus_battery_w_k: float = 0.45
    heater_power_w: float = 22.0
    heater_setpoint_c: float = 4.0
    heater_deadband_c: float = 1.5
    battery_derate_low_c: float = 0.0
    battery_derate_high_c: float = 40.0
    min_effective_capacity_ratio: float = 0.55
    orbit_provider_capability_id: str = "orbit_environment.medium_fidelity.v1"
    eps_provider_capability_id: str | None = None
    thermal_provider_capability_id: str | None = None
    orbit_config: MediumOrbitConfig | OrbitFidelityConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "duration_s", _positive(self.duration_s, "duration_s"))
        object.__setattr__(self, "sample_s", _positive(self.sample_s, "sample_s"))
        if self.sample_s > self.duration_s:
            raise PowerThermalOrbitCoupledError("sample_s must not exceed duration_s")
        for key in ("battery_capacity_wh", "solar_array_max_power_w", "bus_thermal_capacity_j_k", "battery_thermal_capacity_j_k", "radiator_area_m2"):
            object.__setattr__(self, key, _positive(getattr(self, key), key))
        for key in ("bus_load_power_w", "payload_load_power_w", "adcs_load_power_w", "comm_load_power_w", "solar_heat_w", "heater_power_w"):
            object.__setattr__(self, key, _nonnegative(getattr(self, key), key))
        for key in ("initial_soc", "eps_efficiency", "electrical_load_heat_fraction", "min_effective_capacity_ratio"):
            object.__setattr__(self, key, _ratio(getattr(self, key), key))
        if self.radiator_emissivity <= 0.0 or self.radiator_emissivity > 1.0:
            raise PowerThermalOrbitCoupledError("radiator_emissivity must be in (0, 1]")

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "PowerThermalOrbitCoupledConfig":
        sim = _mapping(spec.get("simulation"))
        params = _mapping(spec.get("parameters"))
        spacecraft = _mapping(spec.get("spacecraft"))
        eps = _mapping(spacecraft.get("eps"))
        thermal = _mapping(spacecraft.get("thermal"))
        orbit = _mapping(spec.get("orbit_environment"))
        duration_s = float(sim.get("duration_s", 5400.0))
        sample_s = float(sim.get("sample_s", 60.0))
        epoch_utc = str(sim.get("epoch_utc") or "2026-07-05T00:00:00Z")
        metadata = _mapping(spec.get("metadata"))
        visual_assembly = _mapping(metadata.get("visual_assembly"))
        module_selections = _mapping(visual_assembly.get("module_selections"))
        orbit_provider = str(module_selections.get("orbit_environment") or "orbit_environment.medium_fidelity.v1")
        supported_orbit_providers = {"orbit_environment.medium_fidelity.v1", "orbit_environment.orbit_fidelity.v1"}
        if orbit_provider not in supported_orbit_providers:
            raise PowerThermalOrbitCoupledError(f"unsupported registered orbit_environment module selection: {orbit_provider!r}")
        eps_provider_raw = module_selections.get("eps")
        eps_provider = str(eps_provider_raw).strip() if eps_provider_raw else None
        if eps_provider not in {None, "subsystem.eps.source_native.v1"}:
            raise PowerThermalOrbitCoupledError(f"unsupported registered eps module selection: {eps_provider!r}")
        thermal_provider_raw = module_selections.get("thermal")
        thermal_provider = str(thermal_provider_raw).strip() if thermal_provider_raw else None
        if thermal_provider not in {None, "subsystem.thermal.source_native.v1"}:
            raise PowerThermalOrbitCoupledError(f"unsupported registered thermal module selection: {thermal_provider!r}")
        orbit_spec = dict(spec)
        orbit_spec["task_type"] = "orbit_environment"
        orbit_spec["capability_id"] = orbit_provider
        orbit_spec["simulation"] = {**dict(sim), "duration_s": duration_s, "sample_s": sample_s, "epoch_utc": epoch_utc}
        orbit_spec["target"] = {"level": "integrated", "name": "orbit_environment", "mode": "nominal"}
        orbit_spec["orbit_environment"] = dict(orbit) if orbit else {
            "altitude_m": 500_000.0,
            "inclination_deg": 51.6,
            "enable_eclipse": True,
            "sun_model": "constant",
            "sun_vector_eci": [1.0, 0.0, 0.0],
            "ground_station": {"latitude_deg": 0.0, "longitude_deg": 0.0, "min_elevation_deg": 5.0},
        }
        orbit_config = (
            OrbitFidelityConfig.from_task_spec(orbit_spec)
            if orbit_provider == "orbit_environment.orbit_fidelity.v1"
            else MediumOrbitConfig.from_task_spec(orbit_spec)
        )
        return cls(
            duration_s=duration_s,
            sample_s=sample_s,
            epoch_utc=epoch_utc,
            battery_capacity_wh=float(_first(params, eps, key="battery_capacity_wh", default=180.0)),
            initial_soc=float(_first(params, eps, key="initial_soc", default=0.72)),
            solar_array_max_power_w=float(_first(params, eps, key="solar_array_max_power_w", default=_first(params, eps, key="solar_power_w", default=150.0))),
            bus_load_power_w=float(_first(params, eps, key="bus_load_power_w", default=24.0)),
            payload_load_power_w=float(_first(params, eps, key="payload_load_power_w", default=32.0)),
            adcs_load_power_w=float(_first(params, eps, key="adcs_load_power_w", default=8.0)),
            comm_load_power_w=float(_first(params, eps, key="comm_load_power_w", default=6.0)),
            eps_efficiency=float(_first(params, eps, key="eps_efficiency", default=0.97)),
            electrical_load_heat_fraction=float(_first(params, thermal, key="electrical_load_heat_fraction", default=0.88)),
            solar_heat_w=float(_first(params, thermal, key="solar_heat_w", default=50.0)),
            initial_bus_temp_c=float(_first(params, thermal, key="initial_bus_temp_c", default=18.0)),
            initial_battery_temp_c=float(_first(params, thermal, key="initial_battery_temp_c", default=16.0)),
            bus_thermal_capacity_j_k=float(_first(params, thermal, key="bus_thermal_capacity_j_k", default=18_000.0)),
            battery_thermal_capacity_j_k=float(_first(params, thermal, key="battery_thermal_capacity_j_k", default=9_000.0)),
            radiator_area_m2=float(_first(params, thermal, key="radiator_area_m2", default=0.35)),
            radiator_emissivity=float(_first(params, thermal, key="radiator_emissivity", default=0.82)),
            sink_temp_c=float(_first(params, thermal, key="sink_temp_c", default=-35.0)),
            thermal_conductance_bus_battery_w_k=float(_first(params, thermal, key="thermal_conductance_bus_battery_w_k", default=0.45)),
            heater_power_w=float(_first(params, thermal, key="heater_power_w", default=22.0)),
            heater_setpoint_c=float(_first(params, thermal, key="heater_setpoint_c", default=4.0)),
            heater_deadband_c=float(_first(params, thermal, key="heater_deadband_c", default=1.5)),
            battery_derate_low_c=float(_first(params, thermal, key="battery_derate_low_c", default=0.0)),
            battery_derate_high_c=float(_first(params, thermal, key="battery_derate_high_c", default=40.0)),
            min_effective_capacity_ratio=float(_first(params, thermal, key="min_effective_capacity_ratio", default=0.55)),
            orbit_provider_capability_id=orbit_provider,
            eps_provider_capability_id=eps_provider,
            thermal_provider_capability_id=thermal_provider,
            orbit_config=orbit_config,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in self.__dict__.items() if k != "orbit_config"}
        payload["orbit_config"] = self.orbit_config.to_dict() if self.orbit_config else None
        return payload


def _radiator_heat_w(temp_c: float, sink_temp_c: float, area_m2: float, emissivity: float) -> float:
    sigma = 5.670374419e-8
    temp_k = max(1.0, temp_c + 273.15)
    sink_k = max(1.0, sink_temp_c + 273.15)
    return max(0.0, sigma * emissivity * area_m2 * (temp_k**4 - sink_k**4))


def _battery_capacity_ratio(temp_c: float, config: PowerThermalOrbitCoupledConfig) -> float:
    if temp_c < config.battery_derate_low_c:
        deficit = config.battery_derate_low_c - temp_c
        ratio = 1.0 - 0.015 * deficit
    elif temp_c > config.battery_derate_high_c:
        excess = temp_c - config.battery_derate_high_c
        ratio = 1.0 - 0.010 * excess
    else:
        ratio = 1.0
    return max(config.min_effective_capacity_ratio, min(1.0, ratio))



def _propagate_registered_modules(
    config: PowerThermalOrbitCoupledConfig,
    orbit_samples: list[Any],
    earth_radius_m: float,
) -> list[dict[str, Any]]:
    """Run V7 parent-managed co-simulation for explicitly selected modules.

    The parent owns scheduling and signal transforms.  Selected source-native
    modules execute their existing project step APIs; unselected peers retain
    the legacy parent equations so V6 baseline behavior remains unchanged.
    Stateful feedback is advanced one parent sample at a time.
    """

    use_source_eps = config.eps_provider_capability_id == "subsystem.eps.source_native.v1"
    use_source_thermal = config.thermal_provider_capability_id == "subsystem.thermal.source_native.v1"

    eps_cfg = eps_state = None
    if use_source_eps:
        from subsystems.eps.builder import build_nominal_eps_config
        from subsystems.eps.model import initialize_eps_state
        base = build_nominal_eps_config()
        bus_limit = max(1.0, config.bus_load_power_w + config.payload_load_power_w + config.adcs_load_power_w + config.comm_load_power_w + config.heater_power_w + 5.0)
        eps_cfg = replace(
            base,
            battery=replace(base.battery, capacity_wh=config.battery_capacity_wh, initial_soc=config.initial_soc, charge_efficiency=config.eps_efficiency, discharge_efficiency=config.eps_efficiency),
            solar_panel=replace(base.solar_panel, max_power_w=config.solar_array_max_power_w, efficiency=config.eps_efficiency),
            pdu=replace(base.pdu, bus_max_w=bus_limit, shed_order=("payload", "comm")),
            loads={},
            low_soc_threshold=0.30,
            low_soc_shed_loads=("payload", "comm"),
        )
        eps_state = initialize_eps_state(eps_cfg)

    thermal_cfg = thermal_state = None
    if use_source_thermal:
        from subsystems.thermal.builder import build_nominal_thermal_config
        from subsystems.thermal.model import initialize_thermal_state
        base = build_nominal_thermal_config()
        temps = dict(base.initial_temp_k_by_node)
        temps["battery"] = config.initial_battery_temp_c + 273.15
        temps["electronics"] = config.initial_bus_temp_c + 273.15
        temps["payload"] = config.initial_bus_temp_c + 273.15
        heaters = dict(base.heaters)
        if "battery" in heaters:
            heaters["battery"] = replace(
                heaters["battery"],
                max_power_w=config.heater_power_w,
                setpoint_k=config.heater_setpoint_c + 273.15,
                hysteresis_k=config.heater_deadband_c,
            )
        thermal_cfg = replace(
            base,
            initial_temp_k_by_node=temps,
            mode_power_w_by_node={},
            heat_efficiency_by_component={**dict(base.heat_efficiency_by_component), "eps": 1.0},
            sunlight_heat_w_by_node={
                "battery": config.solar_heat_w * 0.20,
                "electronics": config.solar_heat_w * 0.35,
                "payload": config.solar_heat_w * 0.45,
            },
            heaters=heaters,
        )
        thermal_state = initialize_thermal_state(thermal_cfg)

    storage_wh = config.battery_capacity_wh * config.initial_soc
    bus_temp_c = config.initial_bus_temp_c
    battery_temp_c = config.initial_battery_temp_c
    heater_request_w = 0.0
    last_eps_requested_w = config.bus_load_power_w + config.payload_load_power_w + config.adcs_load_power_w + config.comm_load_power_w
    last_eps_served_w = last_eps_requested_w
    last_net_power_w = config.solar_array_max_power_w * config.eps_efficiency - last_eps_served_w
    last_shed: tuple[str, ...] = ()
    rows: list[dict[str, Any]] = []

    for idx, sample in enumerate(orbit_samples):
        time_s = sample.time_s
        shadow = max(0.0, min(1.0, sample.shadow_factor))
        if use_source_eps and eps_state is not None:
            soc = float(eps_state.battery.soc)
            storage_wh = float(eps_state.battery.storage_wh)
            effective_capacity_wh = float(eps_state.battery.capacity_wh)
            solar_w = config.solar_array_max_power_w * config.eps_efficiency * shadow
            total_load_w = last_eps_requested_w
            net_power_w = last_net_power_w
            payload_derate = 0.0 if "payload" in last_shed else 1.0
        else:
            effective_capacity_wh = config.battery_capacity_wh * _battery_capacity_ratio(battery_temp_c, config)
            storage_wh = min(max(0.0, storage_wh), effective_capacity_wh)
            soc = storage_wh / effective_capacity_wh if effective_capacity_wh > 0.0 else 0.0
            payload_derate = 0.5 if (soc < 0.20 or battery_temp_c < -5.0 or battery_temp_c > 45.0) else 1.0
            total_load_w = config.bus_load_power_w + config.payload_load_power_w * payload_derate + config.adcs_load_power_w + config.comm_load_power_w + heater_request_w
            solar_w = config.solar_array_max_power_w * shadow
            net_power_w = solar_w * config.eps_efficiency - total_load_w
            last_eps_served_w = total_load_w
            last_shed = ()

        internal_heat_w = last_eps_served_w * config.electrical_load_heat_fraction + abs(net_power_w) * 0.03
        solar_heat_w = config.solar_heat_w * shadow
        radiator_w = _radiator_heat_w(bus_temp_c, config.sink_temp_c, config.radiator_area_m2, config.radiator_emissivity)
        conduct_bus_to_battery_w = config.thermal_conductance_bus_battery_w_k * (bus_temp_c - battery_temp_c)
        if idx < len(orbit_samples) - 1:
            dt_s = orbit_samples[idx + 1].time_s - time_s
        else:
            dt_s = 0.0

        rows.append({
            "task_id": "", "case_id": "case_000", "time_s": round(time_s, 12), "sample_index": idx,
            "utc": sample.utc, "target_level": "whole_spacecraft", "target_name": "power_thermal_orbit_coupled", "mode": "nominal",
            "capability_id": "whole_spacecraft.power_thermal_orbit_coupled.v1", "fidelity_level": "medium",
            "spacecraft.time_s": round(time_s, 12),
            "orbit.r_bn_n_m_x": sample.cartesian.position_m[0], "orbit.r_bn_n_m_y": sample.cartesian.position_m[1], "orbit.r_bn_n_m_z": sample.cartesian.position_m[2],
            "orbit.v_bn_n_m_s_x": sample.cartesian.velocity_m_s[0], "orbit.v_bn_n_m_s_y": sample.cartesian.velocity_m_s[1], "orbit.v_bn_n_m_s_z": sample.cartesian.velocity_m_s[2],
            "orbit.radius_m": sample.cartesian.radius_m, "orbit.altitude_m": sample.cartesian.radius_m - earth_radius_m,
            "environment.sun_vector_n_x": sample.sun_vector_eci[0], "environment.sun_vector_n_y": sample.sun_vector_eci[1], "environment.sun_vector_n_z": sample.sun_vector_eci[2],
            "environment.shadow_factor": shadow, "environment.eclipse_flag": bool(sample.eclipse_flag),
            "environment.umbra_flag": bool(getattr(sample, "umbra_flag", shadow <= 1.0e-12)), "environment.penumbra_flag": bool(getattr(sample, "penumbra_flag", 1.0e-12 < shadow < 1.0)),
            "environment.orbit_provider_capability_id": config.orbit_provider_capability_id,
            "eps.module_provider_capability_id": config.eps_provider_capability_id or "parent_internal",
            "thermal.module_provider_capability_id": config.thermal_provider_capability_id or "parent_internal",
            "eps.solar.array_power_w": solar_w,
            "eps.loads.requested.bus_w": config.bus_load_power_w,
            "eps.loads.requested.payload_w": config.payload_load_power_w * payload_derate,
            "eps.loads.requested.adcs_w": config.adcs_load_power_w,
            "eps.loads.requested.comm_w": config.comm_load_power_w,
            "eps.loads.requested.heater_w": heater_request_w,
            "eps.loads.requested_power_w": total_load_w,
            "eps.loads.served_power_w": last_eps_served_w,
            "eps.loads.shed": ",".join(last_shed),
            "eps.battery.storage_wh": storage_wh, "eps.battery.capacity_wh": config.battery_capacity_wh,
            "eps.battery.effective_capacity_wh": effective_capacity_wh, "eps.battery.soc": soc,
            "eps.battery.net_power_w": net_power_w, "eps.power.margin_w": net_power_w, "eps.derating.payload_fraction": payload_derate,
            "thermal.node.bus_temp_c": bus_temp_c, "thermal.node.battery_temp_c": battery_temp_c,
            "thermal.heat.internal_power_w": internal_heat_w, "thermal.heat.solar_input_w": solar_heat_w,
            "thermal.heater.power_w": heater_request_w, "thermal.radiator.heat_reject_w": radiator_w,
            "thermal.coupling.bus_battery_heat_w": conduct_bus_to_battery_w,
            "coupled.energy.solar_to_eps_w": solar_w, "coupled.energy.eps_to_thermal_heat_w": internal_heat_w,
            "coupled.energy.heater_feedback_w": heater_request_w,
            "coupled.derating.battery_capacity_ratio": effective_capacity_wh / config.battery_capacity_wh,
            "label.eclipse_active": bool(sample.eclipse_flag), "label.heater_active": heater_request_w > 0.0,
            "label.payload_derated": payload_derate < 1.0,
            "label.health_state": "payload_derated" if payload_derate < 1.0 else "eclipse" if sample.eclipse_flag else "nominal",
        })
        if dt_s <= 0.0:
            continue

        # EPS executes first with heater/battery-temperature feedback from the prior parent sample.
        if use_source_eps and eps_cfg is not None and eps_state is not None:
            from components.battery.schemas import BatteryState
            from subsystems.eps.model import step_eps
            from subsystems.eps.schemas import EpsStepInput
            effective_capacity = config.battery_capacity_wh * _battery_capacity_ratio(battery_temp_c, config)
            batt = eps_state.battery
            storage = min(float(batt.storage_wh), effective_capacity)
            eps_state = replace(eps_state, battery=BatteryState(storage, effective_capacity, storage / effective_capacity if effective_capacity > 0 else 0.0, batt.shunt_dissipated_wh))
            step_cfg = replace(eps_cfg, battery=replace(eps_cfg.battery, capacity_wh=effective_capacity))
            requested = {
                "obc": config.bus_load_power_w,
                "payload": config.payload_load_power_w,
                "adcs": config.adcs_load_power_w,
                "comm": config.comm_load_power_w,
                "heater": heater_request_w,
            }
            eps_state, eps_result = step_eps(eps_state, step_cfg, EpsStepInput(dt_s=dt_s, mode="nominal", shadow_factor=shadow, requested_loads_w=requested, battery_temp_c=battery_temp_c))
            last_eps_requested_w = float(eps_result.load_requested_w)
            last_eps_served_w = float(eps_result.load_served_w)
            last_net_power_w = float(eps_result.net_power_w)
            last_shed = tuple(dict.fromkeys((*eps_result.shed_loads, *eps_result.low_soc_shed_loads)))
            storage_wh = float(eps_state.battery.storage_wh)
        else:
            storage_wh = storage_wh + net_power_w * dt_s / 3600.0
            storage_wh = min(max(0.0, storage_wh), effective_capacity_wh)
            last_eps_requested_w = total_load_w
            last_eps_served_w = total_load_w
            last_net_power_w = net_power_w

        # Thermal consumes current EPS heat and produces heater/temp feedback for the next parent sample.
        if use_source_thermal and thermal_cfg is not None and thermal_state is not None:
            from subsystems.thermal.model import step_thermal
            from subsystems.thermal.schemas import ThermalStepInput
            thermal_state, thermal_result = step_thermal(
                thermal_state,
                thermal_cfg,
                ThermalStepInput(dt_s=dt_s, mode="nominal", shadow_factor=shadow, component_power_w={"eps": internal_heat_w}, heater_enabled=True),
            )
            bus_temp_c = float(thermal_result.node_temperature_k.get("electronics", bus_temp_c + 273.15)) - 273.15
            battery_temp_c = float(thermal_result.node_temperature_k.get("battery", battery_temp_c + 273.15)) - 273.15
            heater_request_w = sum(float(value) for value in thermal_result.heater_power_w.values())
        else:
            bus_heat_w = internal_heat_w + solar_heat_w + heater_request_w - radiator_w - conduct_bus_to_battery_w
            batt_heat_w = 0.04 * abs(last_net_power_w) + conduct_bus_to_battery_w
            bus_temp_c += bus_heat_w * dt_s / config.bus_thermal_capacity_j_k
            battery_temp_c += batt_heat_w * dt_s / config.battery_thermal_capacity_j_k
            heater_on = min(bus_temp_c, battery_temp_c) <= config.heater_setpoint_c - config.heater_deadband_c
            heater_request_w = config.heater_power_w if heater_on else 0.0

    return rows


def propagate_power_thermal_orbit_coupled(config: PowerThermalOrbitCoupledConfig) -> list[dict[str, Any]]:
    """Run the HF-5 coupled model and return trace rows."""

    grid = build_time_grid(duration_s=config.duration_s, sample_s=config.sample_s, epoch_utc=config.epoch_utc)
    if config.orbit_provider_capability_id == "orbit_environment.orbit_fidelity.v1":
        orbit_config = config.orbit_config
        if not isinstance(orbit_config, OrbitFidelityConfig):
            orbit_config = OrbitFidelityConfig.from_task_spec({"simulation": {"duration_s": config.duration_s, "sample_s": config.sample_s, "epoch_utc": config.epoch_utc}, "orbit_environment": {"altitude_m": 500_000.0}})
        orbit_samples = propagate_orbit_fidelity(orbit_config, grid)
        earth_radius_m = orbit_config.force_model.earth_radius_m
    else:
        orbit_config = config.orbit_config
        if not isinstance(orbit_config, MediumOrbitConfig):
            orbit_config = MediumOrbitConfig.from_task_spec({"simulation": {"duration_s": config.duration_s, "sample_s": config.sample_s, "epoch_utc": config.epoch_utc}, "orbit_environment": {"altitude_m": 500_000.0}})
        orbit_samples = propagate_medium_orbit_environment(orbit_config, grid)
        earth_radius_m = orbit_config.earth_radius_m
    if config.eps_provider_capability_id or config.thermal_provider_capability_id:
        return _propagate_registered_modules(config, list(orbit_samples), earth_radius_m)
    storage_wh = config.battery_capacity_wh * config.initial_soc
    bus_temp_c = config.initial_bus_temp_c
    battery_temp_c = config.initial_battery_temp_c
    rows: list[dict[str, Any]] = []
    for idx, sample in enumerate(orbit_samples):
        time_s = sample.time_s
        shadow = max(0.0, min(1.0, sample.shadow_factor))
        effective_capacity_wh = config.battery_capacity_wh * _battery_capacity_ratio(battery_temp_c, config)
        storage_wh = min(max(0.0, storage_wh), effective_capacity_wh)
        soc = storage_wh / effective_capacity_wh if effective_capacity_wh > 0.0 else 0.0
        heater_on = min(bus_temp_c, battery_temp_c) <= config.heater_setpoint_c - config.heater_deadband_c
        heater_load_w = config.heater_power_w if heater_on else 0.0
        payload_derate = 1.0
        if soc < 0.20 or battery_temp_c < -5.0 or battery_temp_c > 45.0:
            payload_derate = 0.5
        payload_load_w = config.payload_load_power_w * payload_derate
        base_load_w = config.bus_load_power_w + payload_load_w + config.adcs_load_power_w + config.comm_load_power_w
        total_load_w = base_load_w + heater_load_w
        solar_w = config.solar_array_max_power_w * shadow
        net_power_w = solar_w * config.eps_efficiency - total_load_w
        internal_heat_w = base_load_w * config.electrical_load_heat_fraction + abs(net_power_w) * 0.03
        solar_heat_w = config.solar_heat_w * shadow
        radiator_w = _radiator_heat_w(bus_temp_c, config.sink_temp_c, config.radiator_area_m2, config.radiator_emissivity)
        conduct_bus_to_battery_w = config.thermal_conductance_bus_battery_w_k * (bus_temp_c - battery_temp_c)
        if idx < len(orbit_samples) - 1:
            dt_s = orbit_samples[idx + 1].time_s - time_s
        else:
            dt_s = 0.0
        rows.append({
            "task_id": "",
            "case_id": "case_000",
            "time_s": round(time_s, 12),
            "sample_index": idx,
            "utc": sample.utc,
            "target_level": "whole_spacecraft",
            "target_name": "power_thermal_orbit_coupled",
            "mode": "nominal",
            "capability_id": "whole_spacecraft.power_thermal_orbit_coupled.v1",
            "fidelity_level": "medium",
            "spacecraft.time_s": round(time_s, 12),
            "orbit.r_bn_n_m_x": sample.cartesian.position_m[0],
            "orbit.r_bn_n_m_y": sample.cartesian.position_m[1],
            "orbit.r_bn_n_m_z": sample.cartesian.position_m[2],
            "orbit.v_bn_n_m_s_x": sample.cartesian.velocity_m_s[0],
            "orbit.v_bn_n_m_s_y": sample.cartesian.velocity_m_s[1],
            "orbit.v_bn_n_m_s_z": sample.cartesian.velocity_m_s[2],
            "orbit.radius_m": sample.cartesian.radius_m,
            "orbit.altitude_m": sample.cartesian.radius_m - earth_radius_m,
            "environment.sun_vector_n_x": sample.sun_vector_eci[0],
            "environment.sun_vector_n_y": sample.sun_vector_eci[1],
            "environment.sun_vector_n_z": sample.sun_vector_eci[2],
            "environment.shadow_factor": shadow,
            "environment.eclipse_flag": bool(sample.eclipse_flag),
            "environment.umbra_flag": bool(getattr(sample, "umbra_flag", shadow <= 1.0e-12)),
            "environment.penumbra_flag": bool(getattr(sample, "penumbra_flag", 1.0e-12 < shadow < 1.0)),
            "environment.orbit_provider_capability_id": config.orbit_provider_capability_id,
            "eps.solar.array_power_w": solar_w,
            "eps.loads.requested.bus_w": config.bus_load_power_w,
            "eps.loads.requested.payload_w": payload_load_w,
            "eps.loads.requested.adcs_w": config.adcs_load_power_w,
            "eps.loads.requested.comm_w": config.comm_load_power_w,
            "eps.loads.requested.heater_w": heater_load_w,
            "eps.loads.requested_power_w": total_load_w,
            "eps.battery.storage_wh": storage_wh,
            "eps.battery.capacity_wh": config.battery_capacity_wh,
            "eps.battery.effective_capacity_wh": effective_capacity_wh,
            "eps.battery.soc": soc,
            "eps.battery.net_power_w": net_power_w,
            "eps.power.margin_w": net_power_w,
            "eps.derating.payload_fraction": payload_derate,
            "thermal.node.bus_temp_c": bus_temp_c,
            "thermal.node.battery_temp_c": battery_temp_c,
            "thermal.heat.internal_power_w": internal_heat_w,
            "thermal.heat.solar_input_w": solar_heat_w,
            "thermal.heater.power_w": heater_load_w,
            "thermal.radiator.heat_reject_w": radiator_w,
            "thermal.coupling.bus_battery_heat_w": conduct_bus_to_battery_w,
            "coupled.energy.solar_to_eps_w": solar_w,
            "coupled.energy.eps_to_thermal_heat_w": internal_heat_w,
            "coupled.energy.heater_feedback_w": heater_load_w,
            "coupled.derating.battery_capacity_ratio": effective_capacity_wh / config.battery_capacity_wh,
            "label.eclipse_active": bool(sample.eclipse_flag),
            "label.heater_active": heater_load_w > 0.0,
            "label.payload_derated": payload_derate < 1.0,
            "label.health_state": "payload_derated" if payload_derate < 1.0 else "eclipse" if sample.eclipse_flag else "nominal",
        })
        if dt_s > 0.0:
            storage_wh = storage_wh + net_power_w * dt_s / 3600.0
            storage_wh = min(max(0.0, storage_wh), effective_capacity_wh)
            bus_heat_w = internal_heat_w + solar_heat_w + heater_load_w - radiator_w - conduct_bus_to_battery_w
            batt_heat_w = 0.04 * abs(net_power_w) + conduct_bus_to_battery_w
            bus_temp_c += bus_heat_w * dt_s / config.bus_thermal_capacity_j_k
            battery_temp_c += batt_heat_w * dt_s / config.battery_thermal_capacity_j_k
    return rows


def summarize_power_thermal_orbit_coupled(rows: Iterable[Mapping[str, Any]], config: PowerThermalOrbitCoupledConfig) -> dict[str, Any]:
    data = [dict(r) for r in rows]
    if not data:
        raise PowerThermalOrbitCoupledError("cannot summarize empty HF-5 trace")
    soc = [float(r["eps.battery.soc"]) for r in data]
    bus_temp = [float(r["thermal.node.bus_temp_c"]) for r in data]
    batt_temp = [float(r["thermal.node.battery_temp_c"]) for r in data]
    solar = [float(r["eps.solar.array_power_w"]) for r in data]
    load = [float(r["eps.loads.requested_power_w"]) for r in data]
    heater = [float(r["thermal.heater.power_w"]) for r in data]
    eclipse = [1.0 if bool(r.get("environment.eclipse_flag")) else 0.0 for r in data]
    return {
        "schema_version": HF5_POWER_THERMAL_ORBIT_SCHEMA_VERSION,
        "status": "complete",
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "model_family": "route_b_power_thermal_orbit_coupled_proxy",
        "sample_count": len(data),
        "duration_s": config.duration_s,
        "sample_s": config.sample_s,
        "qoi": {
            "spacecraft.power.initial_soc": soc[0],
            "spacecraft.power.final_soc": soc[-1],
            "spacecraft.power.min_soc": min(soc),
            "spacecraft.power.max_soc": max(soc),
            "spacecraft.power.min_margin_w": min(float(r["eps.power.margin_w"]) for r in data),
            "spacecraft.power.solar_energy_wh": sum(solar[:-1]) * config.sample_s / 3600.0 if len(solar) > 1 else solar[0] * config.duration_s / 3600.0,
            "spacecraft.power.load_energy_wh": sum(load[:-1]) * config.sample_s / 3600.0 if len(load) > 1 else load[0] * config.duration_s / 3600.0,
            "spacecraft.thermal.initial_bus_temp_c": bus_temp[0],
            "spacecraft.thermal.final_bus_temp_c": bus_temp[-1],
            "spacecraft.thermal.min_bus_temp_c": min(bus_temp),
            "spacecraft.thermal.max_bus_temp_c": max(bus_temp),
            "spacecraft.thermal.final_battery_temp_c": batt_temp[-1],
            "spacecraft.thermal.min_battery_temp_c": min(batt_temp),
            "spacecraft.thermal.max_battery_temp_c": max(batt_temp),
            "spacecraft.thermal.heater_energy_wh": sum(heater[:-1]) * config.sample_s / 3600.0 if len(heater) > 1 else heater[0] * config.duration_s / 3600.0,
            "spacecraft.environment.eclipse_fraction": sum(eclipse) / len(eclipse),
            "spacecraft.derating.payload_derated_samples": sum(1 for r in data if bool(r.get("label.payload_derated"))),
        },
        "events": {
            "eclipse_samples": sum(1 for r in data if bool(r.get("environment.eclipse_flag"))),
            "heater_active_samples": sum(1 for r in data if bool(r.get("label.heater_active"))),
            "payload_derated_samples": sum(1 for r in data if bool(r.get("label.payload_derated"))),
        },
        "config": config.to_dict(),
        "known_physics_limits": [
            "Thermal dynamics are two lumped nodes; no finite-element or multi-node spacecraft thermal network is implemented.",
            "Battery temperature derating is a deterministic envelope proxy, not electrochemical validation.",
            "EPS load shedding is simplified to payload derating; PDU switching transients are not modeled.",
            ("Orbit forcing uses the registered ORB-1 numerical orbit-fidelity provider, but remains a prototype rather than flight-grade truth." if config.orbit_provider_capability_id == "orbit_environment.orbit_fidelity.v1" else "Orbit forcing uses the registered HF-3 medium-fidelity shadow geometry, not a full force-model integrator."),
        ],
    }


def build_hf5_power_thermal_orbit_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        config = PowerThermalOrbitCoupledConfig.from_task_spec(spec) if spec else None
        config_payload = config.to_dict() if config else None
        status = "implemented_medium_coupled_not_high_fidelity"
        validation_status = "config_valid" if config else "metadata_only"
    except Exception as exc:
        config_payload = {"error": str(exc)}
        status = "configured_with_validation_error"
        validation_status = "config_invalid"
    orbit_provider = (config.orbit_provider_capability_id if config else "orbit_environment.medium_fidelity.v1")
    orbit_payload = (
        build_orb1_orbit_fidelity_payload(spec)
        if spec and orbit_provider == "orbit_environment.orbit_fidelity.v1"
        else build_hf3_orbit_environment_payload(spec) if spec else None
    )
    return {
        "schema_version": HF5_POWER_THERMAL_ORBIT_SCHEMA_VERSION,
        "route_b_version": "B-4/HF-5",
        "foundation_dependencies": ["B-1/HF-1+HF-2", "ORB-1" if orbit_provider == "orbit_environment.orbit_fidelity.v1" else "B-2/HF-3"],
        "status": status,
        "validation_status": validation_status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_couplings": [
            "orbit eclipse/shadow factor -> solar array generation",
            "orbit eclipse/shadow factor -> thermal solar heat input",
            "electrical load -> thermal internal heat",
            "thermal heater demand -> EPS heater load",
            "battery temperature -> effective capacity derating",
        ],
        "uses_route_b_models": [
            orbit_provider,
            "HF-1/HF-2 time, frame, state, and solver metadata",
        ],
        "selected_orbit_provider_capability_id": orbit_provider,
        "config": config_payload,
        "orbit_environment_payload": orbit_payload,
        "remaining_route_b_dependencies": [
            "HF-6 comm/payload mission coupling",
            "HF-7 propulsion-orbit-attitude coupling",
            "HF-8 physical validation gates",
            "HF-9 benchmark scenarios and tolerance envelopes",
        ],
    }


__all__ = [
    "HF5_POWER_THERMAL_ORBIT_SCHEMA_VERSION",
    "PowerThermalOrbitCoupledConfig",
    "PowerThermalOrbitCoupledError",
    "build_hf5_power_thermal_orbit_payload",
    "propagate_power_thermal_orbit_coupled",
    "summarize_power_thermal_orbit_coupled",
]
