"""HF-6 communication/payload mission-coupled spacecraft model.

The model couples payload data generation, onboard storage, ground-station
access, transmitter downlink, and a compact EPS power constraint.  It is a
Route-B mission prototype, not a high-fidelity RF/contact-planning model.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping
import math

from sat_sim.orbit import MediumOrbitConfig, propagate_medium_orbit_environment, build_hf3_orbit_environment_payload
from sat_sim.time_systems import build_time_grid

HF6_COMM_PAYLOAD_MISSION_SCHEMA_VERSION = "hf6.comm_payload_mission_coupled.v1"


class CommPayloadMissionCoupledError(ValueError):
    """Raised when HF-6 mission inputs are invalid."""


def _finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(float(value)):
        raise CommPayloadMissionCoupledError(f"{name} must be a finite number")
    return float(value)


def _positive(value: Any, name: str) -> float:
    val = _finite(value, name)
    if val <= 0.0:
        raise CommPayloadMissionCoupledError(f"{name} must be positive")
    return val


def _nonnegative(value: Any, name: str) -> float:
    val = _finite(value, name)
    if val < 0.0:
        raise CommPayloadMissionCoupledError(f"{name} must be non-negative")
    return val


def _ratio(value: Any, name: str) -> float:
    val = _finite(value, name)
    if not 0.0 <= val <= 1.0:
        raise CommPayloadMissionCoupledError(f"{name} must be in [0, 1]")
    return val


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first(*mappings: Mapping[str, Any], key: str, default: Any) -> Any:
    for mapping in mappings:
        if key in mapping and mapping.get(key) is not None:
            return mapping.get(key)
    return default


@dataclass(frozen=True)
class CommPayloadMissionCoupledConfig:
    duration_s: float = 5400.0
    sample_s: float = 60.0
    epoch_utc: str = "2026-07-05T00:00:00Z"
    payload_data_rate_bps: float = 2.0e6
    payload_power_w: float = 35.0
    storage_capacity_bits: float = 8.0e9
    initial_stored_bits: float = 1.0e9
    downlink_rate_bps: float = 12.0e6
    transmitter_power_w: float = 18.0
    comm_idle_power_w: float = 4.0
    bus_power_w: float = 22.0
    solar_array_max_power_w: float = 150.0
    battery_capacity_wh: float = 180.0
    initial_soc: float = 0.75
    eps_efficiency: float = 0.97
    min_soc_for_payload: float = 0.15
    min_soc_for_downlink: float = 0.20
    pointing_required: bool = False
    pointing_available: bool = True
    orbit_config: MediumOrbitConfig | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "duration_s", _positive(self.duration_s, "duration_s"))
        object.__setattr__(self, "sample_s", _positive(self.sample_s, "sample_s"))
        if self.sample_s > self.duration_s:
            raise CommPayloadMissionCoupledError("sample_s must not exceed duration_s")
        for key in ("payload_data_rate_bps", "storage_capacity_bits", "downlink_rate_bps", "battery_capacity_wh", "solar_array_max_power_w"):
            object.__setattr__(self, key, _positive(getattr(self, key), key))
        for key in ("payload_power_w", "transmitter_power_w", "comm_idle_power_w", "bus_power_w", "initial_stored_bits"):
            object.__setattr__(self, key, _nonnegative(getattr(self, key), key))
        for key in ("initial_soc", "eps_efficiency", "min_soc_for_payload", "min_soc_for_downlink"):
            object.__setattr__(self, key, _ratio(getattr(self, key), key))
        if self.initial_stored_bits > self.storage_capacity_bits:
            object.__setattr__(self, "initial_stored_bits", self.storage_capacity_bits)

    @classmethod
    def from_task_spec(cls, spec: Mapping[str, Any]) -> "CommPayloadMissionCoupledConfig":
        sim = _mapping(spec.get("simulation"))
        params = _mapping(spec.get("parameters"))
        spacecraft = _mapping(spec.get("spacecraft"))
        payload = _mapping(spacecraft.get("payload"))
        comm = _mapping(spacecraft.get("comm_data"))
        eps = _mapping(spacecraft.get("eps"))
        mission = _mapping(spacecraft.get("mission"))
        orbit = _mapping(spec.get("orbit_environment"))
        duration_s = float(sim.get("duration_s", 5400.0))
        sample_s = float(sim.get("sample_s", 60.0))
        epoch_utc = str(sim.get("epoch_utc") or "2026-07-05T00:00:00Z")
        orbit_payload = dict(orbit) if orbit else {
            "altitude_m": 500_000.0,
            "inclination_deg": 51.6,
            "enable_eclipse": True,
            "sun_model": "constant",
            "sun_vector_eci": [1.0, 0.0, 0.0],
            "ground_station": {"latitude_deg": 0.0, "longitude_deg": 0.0, "min_elevation_deg": 5.0},
        }
        if "ground_station" not in orbit_payload:
            orbit_payload["ground_station"] = {"latitude_deg": 0.0, "longitude_deg": 0.0, "min_elevation_deg": 5.0}
        orbit_spec = dict(spec)
        orbit_spec["task_type"] = "orbit_environment"
        orbit_spec["capability_id"] = "orbit_environment.medium_fidelity.v1"
        orbit_spec["target"] = {"level": "integrated", "name": "orbit_environment", "mode": "nominal"}
        orbit_spec["simulation"] = {**dict(sim), "duration_s": duration_s, "sample_s": sample_s, "epoch_utc": epoch_utc}
        orbit_spec["orbit_environment"] = orbit_payload
        orbit_config = MediumOrbitConfig.from_task_spec(orbit_spec)
        return cls(
            duration_s=duration_s,
            sample_s=sample_s,
            epoch_utc=epoch_utc,
            payload_data_rate_bps=float(_first(params, payload, comm, key="payload_data_rate_bps", default=_first(params, payload, comm, key="generated_bps", default=2.0e6))),
            payload_power_w=float(_first(params, payload, eps, key="payload_power_w", default=35.0)),
            storage_capacity_bits=float(_first(params, payload, comm, key="storage_capacity_bits", default=8.0e9)),
            initial_stored_bits=float(_first(params, payload, comm, key="initial_stored_bits", default=1.0e9)),
            downlink_rate_bps=float(_first(params, comm, key="downlink_rate_bps", default=_first(params, comm, key="raw_rate_bps", default=12.0e6))),
            transmitter_power_w=float(_first(params, comm, key="transmitter_power_w", default=_first(params, comm, key="tx_power_w", default=18.0))),
            comm_idle_power_w=float(_first(params, comm, key="comm_idle_power_w", default=4.0)),
            bus_power_w=float(_first(params, eps, key="bus_power_w", default=_first(params, eps, key="bus_load_power_w", default=22.0))),
            solar_array_max_power_w=float(_first(params, eps, key="solar_array_max_power_w", default=_first(params, eps, key="solar_power_w", default=150.0))),
            battery_capacity_wh=float(_first(params, eps, key="battery_capacity_wh", default=180.0)),
            initial_soc=float(_first(params, eps, key="initial_soc", default=0.75)),
            eps_efficiency=float(_first(params, eps, key="eps_efficiency", default=0.97)),
            min_soc_for_payload=float(_first(params, mission, key="min_soc_for_payload", default=0.15)),
            min_soc_for_downlink=float(_first(params, mission, key="min_soc_for_downlink", default=0.20)),
            pointing_required=bool(_first(params, mission, key="pointing_required", default=False)),
            pointing_available=bool(_first(params, mission, key="pointing_available", default=True)),
            orbit_config=orbit_config,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = {k: v for k, v in self.__dict__.items() if k != "orbit_config"}
        payload["orbit_config"] = self.orbit_config.to_dict() if self.orbit_config else None
        return payload


def propagate_comm_payload_mission_coupled(config: CommPayloadMissionCoupledConfig) -> list[dict[str, Any]]:
    grid = build_time_grid(duration_s=config.duration_s, sample_s=config.sample_s, epoch_utc=config.epoch_utc)
    orbit_config = config.orbit_config or MediumOrbitConfig.from_task_spec({"simulation": {"duration_s": config.duration_s, "sample_s": config.sample_s, "epoch_utc": config.epoch_utc}, "orbit_environment": {"altitude_m": 500_000.0, "ground_station": {"latitude_deg": 0.0, "longitude_deg": 0.0}}})
    orbit_samples = propagate_medium_orbit_environment(orbit_config, grid)
    stored_bits = min(config.initial_stored_bits, config.storage_capacity_bits)
    storage_overflow_bits = 0.0
    battery_storage_wh = config.battery_capacity_wh * config.initial_soc
    total_generated_bits = 0.0
    total_downlinked_bits = 0.0
    rows: list[dict[str, Any]] = []
    for idx, sample in enumerate(orbit_samples):
        time_s = sample.time_s
        if idx < len(orbit_samples) - 1:
            dt_s = orbit_samples[idx + 1].time_s - time_s
        else:
            dt_s = 0.0
        soc = max(0.0, min(1.0, battery_storage_wh / config.battery_capacity_wh))
        has_access = bool(sample.ground_has_access)
        pointing_ok = (not config.pointing_required) or config.pointing_available
        payload_enabled = soc >= config.min_soc_for_payload
        generated_bits = config.payload_data_rate_bps * dt_s if payload_enabled else 0.0
        downlink_active = has_access and pointing_ok and soc >= config.min_soc_for_downlink and stored_bits > 0.0
        downlink_capacity_bits = config.downlink_rate_bps * dt_s if downlink_active else 0.0
        available_bits = stored_bits + generated_bits
        downlinked_bits = min(available_bits, downlink_capacity_bits)
        next_stored = available_bits - downlinked_bits
        overflow_bits = max(0.0, next_stored - config.storage_capacity_bits)
        if overflow_bits > 0.0:
            next_stored = config.storage_capacity_bits
        transmitter_power_w = config.transmitter_power_w if downlink_active else 0.0
        payload_power_w = config.payload_power_w if payload_enabled else 0.0
        comm_power_w = config.comm_idle_power_w + transmitter_power_w
        solar_w = config.solar_array_max_power_w * max(0.0, min(1.0, sample.shadow_factor))
        total_load_w = config.bus_power_w + payload_power_w + comm_power_w
        net_power_w = solar_w * config.eps_efficiency - total_load_w
        conservation_error = (config.initial_stored_bits + total_generated_bits + generated_bits) - (total_downlinked_bits + downlinked_bits) - next_stored - (storage_overflow_bits + overflow_bits)
        rows.append({
            "task_id": "",
            "case_id": "case_000",
            "time_s": round(time_s, 12),
            "sample_index": idx,
            "utc": sample.utc,
            "target_level": "whole_spacecraft",
            "target_name": "comm_payload_mission_coupled",
            "mode": "nominal",
            "capability_id": "whole_spacecraft.comm_payload_mission_coupled.v1",
            "fidelity_level": "medium",
            "spacecraft.time_s": round(time_s, 12),
            "orbit.r_bn_n_m_x": sample.cartesian.position_m[0],
            "orbit.r_bn_n_m_y": sample.cartesian.position_m[1],
            "orbit.r_bn_n_m_z": sample.cartesian.position_m[2],
            "orbit.radius_m": sample.cartesian.radius_m,
            "orbit.altitude_m": sample.cartesian.radius_m - orbit_config.earth_radius_m,
            "environment.shadow_factor": sample.shadow_factor,
            "environment.eclipse_flag": bool(sample.eclipse_flag),
            "ground.range_m": sample.ground_range_m,
            "ground.elevation_deg": sample.ground_elevation_deg,
            "ground.has_access": sample.ground_has_access,
            "payload.enabled": payload_enabled,
            "payload.generated_bps": config.payload_data_rate_bps if payload_enabled else 0.0,
            "payload.generated_bits_step": generated_bits,
            "payload.power_w": payload_power_w,
            "comm.transmitter.active": downlink_active,
            "comm.downlink_rate_bps": config.downlink_rate_bps if downlink_active else 0.0,
            "comm.downlinked_bits_step": downlinked_bits,
            "comm.power_w": comm_power_w,
            "storage.stored_bits": stored_bits,
            "storage.next_stored_bits": next_stored,
            "storage.capacity_bits": config.storage_capacity_bits,
            "storage.fill_fraction": stored_bits / config.storage_capacity_bits if config.storage_capacity_bits > 0 else 0.0,
            "storage.overflow_bits_step": overflow_bits,
            "eps.solar.array_power_w": solar_w,
            "eps.loads.requested.bus_w": config.bus_power_w,
            "eps.loads.requested.payload_w": payload_power_w,
            "eps.loads.requested.comm_w": comm_power_w,
            "eps.loads.requested_power_w": total_load_w,
            "eps.battery.soc": soc,
            "eps.battery.storage_wh": battery_storage_wh,
            "eps.power.margin_w": net_power_w,
            "coupled.data.generated_bits_total_if_applied": total_generated_bits + generated_bits,
            "coupled.data.downlinked_bits_total_if_applied": total_downlinked_bits + downlinked_bits,
            "coupled.data.conservation_error_bits": conservation_error,
            "label.access_active": has_access,
            "label.downlink_active": downlink_active,
            "label.payload_enabled": payload_enabled,
            "label.storage_full": next_stored >= config.storage_capacity_bits - 1.0e-9,
            "label.power_constrained": soc < max(config.min_soc_for_payload, config.min_soc_for_downlink),
            "label.health_state": "power_constrained" if soc < max(config.min_soc_for_payload, config.min_soc_for_downlink) else "storage_full" if overflow_bits > 0.0 else "downlink" if downlink_active else "nominal",
        })
        if dt_s > 0.0:
            stored_bits = next_stored
            storage_overflow_bits += overflow_bits
            total_generated_bits += generated_bits
            total_downlinked_bits += downlinked_bits
            battery_storage_wh = max(0.0, min(config.battery_capacity_wh, battery_storage_wh + net_power_w * dt_s / 3600.0))
    return rows


def summarize_comm_payload_mission_coupled(rows: Iterable[Mapping[str, Any]], config: CommPayloadMissionCoupledConfig) -> dict[str, Any]:
    data = [dict(r) for r in rows]
    if not data:
        raise CommPayloadMissionCoupledError("cannot summarize empty HF-6 trace")
    generated = sum(float(r.get("payload.generated_bits_step", 0.0)) for r in data)
    downlinked = sum(float(r.get("comm.downlinked_bits_step", 0.0)) for r in data)
    overflow = sum(float(r.get("storage.overflow_bits_step", 0.0)) for r in data)
    final_stored = float(data[-1].get("storage.next_stored_bits", data[-1].get("storage.stored_bits", 0.0)))
    conservation_error = (config.initial_stored_bits + generated) - downlinked - final_stored - overflow
    stored = [float(r.get("storage.stored_bits", 0.0)) for r in data]
    soc = [float(r.get("eps.battery.soc", 0.0)) for r in data]
    access = [1.0 if bool(r.get("ground.has_access")) else 0.0 for r in data if r.get("ground.has_access") is not None]
    return {
        "schema_version": HF6_COMM_PAYLOAD_MISSION_SCHEMA_VERSION,
        "status": "complete",
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "model_family": "route_b_comm_payload_mission_coupled_proxy",
        "sample_count": len(data),
        "duration_s": config.duration_s,
        "sample_s": config.sample_s,
        "qoi": {
            "mission.data.generated_bits": generated,
            "mission.data.downlinked_bits": downlinked,
            "mission.data.final_stored_bits": final_stored,
            "mission.data.max_stored_bits": max(stored),
            "mission.data.overflow_bits": overflow,
            "mission.data.conservation_error_bits": conservation_error,
            "mission.comm.downlink_active_fraction": sum(1 for r in data if bool(r.get("label.downlink_active"))) / len(data),
            "mission.ground.access_fraction": (sum(access) / len(access)) if access else None,
            "mission.power.min_soc": min(soc),
            "mission.power.final_soc": soc[-1],
        },
        "events": {
            "access_samples": sum(1 for r in data if bool(r.get("ground.has_access"))),
            "downlink_samples": sum(1 for r in data if bool(r.get("label.downlink_active"))),
            "payload_disabled_samples": sum(1 for r in data if not bool(r.get("label.payload_enabled"))),
            "storage_full_samples": sum(1 for r in data if bool(r.get("label.storage_full"))),
            "power_constrained_samples": sum(1 for r in data if bool(r.get("label.power_constrained"))),
        },
        "config": config.to_dict(),
        "known_physics_limits": [
            "Ground access is inherited from HF-3 geometry and does not implement multi-station scheduling.",
            "Downlink is a constant-rate proxy; no Doppler, adaptive coding, antenna pointing, weather, or RF propagation model is included.",
            "EPS power is a compact energy balance, not a full spacecraft electrical network.",
            "Payload operations are constant-rate and do not model instrument modes or observation geometry beyond optional pointing metadata.",
        ],
    }


def build_hf6_comm_payload_mission_payload(task_spec: Mapping[str, Any] | None = None) -> dict[str, Any]:
    spec = task_spec if isinstance(task_spec, Mapping) else {}
    try:
        config = CommPayloadMissionCoupledConfig.from_task_spec(spec) if spec else None
        config_payload = config.to_dict() if config else None
        status = "implemented_medium_mission_coupled_not_high_fidelity"
        validation_status = "config_valid" if config else "metadata_only"
    except Exception as exc:
        config_payload = {"error": str(exc)}
        status = "configured_with_validation_error"
        validation_status = "config_invalid"
    return {
        "schema_version": HF6_COMM_PAYLOAD_MISSION_SCHEMA_VERSION,
        "route_b_version": "B-4/HF-6",
        "foundation_dependencies": ["B-1/HF-1+HF-2", "B-2/HF-3"],
        "status": status,
        "validation_status": validation_status,
        "fidelity_level": "medium",
        "can_claim_high_fidelity": False,
        "implemented_couplings": [
            "payload data generation -> onboard storage growth",
            "ground-station access -> transmitter downlink window",
            "transmitter active state -> EPS load",
            "battery SOC -> payload/downlink power constraint",
            "generated/downlinked/stored/overflow data conservation accounting",
        ],
        "uses_route_b_models": [
            "HF-3 orbit_environment.medium_fidelity.v1 ground access geometry",
            "HF-1/HF-2 time, frame, state, and solver metadata",
        ],
        "config": config_payload,
        "orbit_environment_payload": build_hf3_orbit_environment_payload(spec) if spec else None,
        "remaining_route_b_dependencies": [
            "HF-7 propulsion-orbit-attitude coupling",
            "HF-8 physical validation gates",
            "HF-9 benchmark scenarios and tolerance envelopes",
        ],
    }


__all__ = [
    "HF6_COMM_PAYLOAD_MISSION_SCHEMA_VERSION",
    "CommPayloadMissionCoupledConfig",
    "CommPayloadMissionCoupledError",
    "build_hf6_comm_payload_mission_payload",
    "propagate_comm_payload_mission_coupled",
    "summarize_comm_payload_mission_coupled",
]
