"""Strict, provenance-aware pre-start configuration for continuous native whole spacecraft.

Only native-wired values are exposed. This is not a generic component editor:
a parameter must be read by the actual Basilisk unified graph and validated
before it can reach a fresh runtime. No arbitrary tasks, paths or Python code.
"""
from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class NativeOrbitInitial(_Strict):
    spacecraft_mass_kg: float = Field(
        default=100., ge=1., le=2000.,
        description="整星原生 Spacecraft.hub.mHub 总质量（kg）；包含当前图采用的总质量假设，不单独改变惯量矩阵。",
    )
    orbit_radius_m: float = Field(default=7_000_000., ge=6_500_000., le=20_000_000.)
    eccentricity: float = Field(default=.001, ge=0., le=.2)
    inclination_deg: float = Field(default=35., ge=0., le=180.)
    initial_true_anomaly_rad: float = Field(default=0., ge=0., le=6.283185307179586)


class NativeAdcsInitial(_Strict):
    sun_sensor_fov_deg: float = Field(
        default=80., gt=0., le=180.,
        description="原生 Basilisk CoarseSunSensor.fov 视场全角（deg），仅影响该光敏器观测，不代表所有敏感器的视场。",
    )
    initial_pointing_error_deg: float = Field(default=8., ge=0., le=90.)
    controller_k: float = Field(default=3.5, gt=0., le=100.)
    controller_p: float = Field(default=30., gt=0., le=300.)
    rw_configuration: Literal["orthogonal_3", "pyramid_4"] = "orthogonal_3"
    rw_max_torque_nm: float = Field(
        default=.2, gt=0., le=.2,
        description="真实 Basilisk RW 电机 u_max 与指令故障管理器的共同限幅 (N·m)",
    )
    rw_initial_speed_rad_s_0: float = Field(default=10.471975511965978, ge=-500., le=500., description="RW1 原生初始轮速 (rad/s)")
    rw_initial_speed_rad_s_1: float = Field(default=20.943951023931955, ge=-500., le=500., description="RW2 原生初始轮速 (rad/s)")
    rw_initial_speed_rad_s_2: float = Field(default=31.41592653589793, ge=-500., le=500., description="RW3 原生初始轮速 (rad/s)")
    rw_initial_speed_rad_s_3: float | None = Field(default=None, ge=-500., le=500.,
                                                   description="仅 4 轮金字塔构型可设置 RW4 初始轮速 (rad/s)")

    @model_validator(mode="after")
    def wheel_count_matches_hardware(self):
        if self.rw_configuration != "pyramid_4" and self.rw_initial_speed_rad_s_3 is not None:
            raise ValueError("RW4_INITIAL_SPEED_REQUIRES_PYRAMID_4")
        return self


class NativeEpsInitial(_Strict):
    battery_capacity_wh: float = Field(default=160., gt=0., le=2000.)
    initial_soc: float = Field(default=.62, ge=0.05, le=1.)
    solar_panel_area_m2: float = Field(default=2.5, gt=0., le=25.)
    solar_efficiency: float = Field(default=.28, gt=0., le=1.)
    bus_power_w: float = Field(default=18., ge=0., le=300.)


class NativePayloadInitial(_Strict):
    payload_data_rate_bps: float = Field(default=2_500_000., ge=0., le=20_000_000.)
    payload_power_w: float = Field(default=38., ge=0., le=250.)


class NativeCommInitial(_Strict):
    storage_capacity_bits: float = Field(default=6_000_000_000., gt=0., le=100_000_000_000.)
    downlink_rate_bps: float = Field(default=1_500_000., gt=0., le=20_000_000.)


class ContinuousInitialConfiguration(_Strict):
    """Editable subsystems/devices of the *integrated* native spacecraft."""

    orbit: NativeOrbitInitial = Field(default_factory=NativeOrbitInitial)
    adcs: NativeAdcsInitial = Field(default_factory=NativeAdcsInitial)
    eps: NativeEpsInitial = Field(default_factory=NativeEpsInitial)
    payload: NativePayloadInitial = Field(default_factory=NativePayloadInitial)
    comm_data: NativeCommInitial = Field(default_factory=NativeCommInitial)

    def native_values(self) -> dict[str, Any]:
        # Only explicitly chosen groups/values are forwarded. Omitted fields
        # retain existing Basilisk graph defaults, including future changes.
        values: dict[str, Any] = {}
        for group in ("orbit", "adcs", "eps", "payload", "comm_data"):
            model = getattr(self, group)
            if group in self.model_fields_set:
                values.update(model.model_dump(exclude_unset=True))
        return values


_DISPLAY = {
    "orbit": "整星质量与轨道初始条件",
    "adcs": "姿态控制、太阳敏感器与反作用轮（ADCS／单机）",
    "eps": "电池与太阳电池板（EPS／单机）",
    "payload": "载荷设备",
    "comm_data": "通信数据存储",
}

_UNITS = {
    "spacecraft_mass_kg": "kg", "sun_sensor_fov_deg": "deg",
    "orbit_radius_m": "m", "eccentricity": "1", "inclination_deg": "deg",
    "initial_true_anomaly_rad": "rad",
    "initial_pointing_error_deg": "deg", "controller_k": "1", "controller_p": "1",
    "rw_configuration": "枚举", "rw_max_torque_nm": "N·m",
    "rw_initial_speed_rad_s_0": "rad/s", "rw_initial_speed_rad_s_1": "rad/s",
    "rw_initial_speed_rad_s_2": "rad/s", "rw_initial_speed_rad_s_3": "rad/s",
    "battery_capacity_wh": "Wh", "initial_soc": "1", "solar_panel_area_m2": "m²",
    "solar_efficiency": "1", "bus_power_w": "W",
    "payload_data_rate_bps": "bit/s", "payload_power_w": "W",
    "storage_capacity_bits": "bit", "downlink_rate_bps": "bit/s",
}


def initial_configuration_catalog() -> dict[str, Any]:
    """Single source for form defaults, supported controls and native scope."""
    groups = []
    for name, cls in (
        ("orbit", NativeOrbitInitial),
        ("adcs", NativeAdcsInitial),
        ("eps", NativeEpsInitial),
        ("payload", NativePayloadInitial),
        ("comm_data", NativeCommInitial),
    ):
        template = cls()
        fields = []
        for key, field in cls.model_fields.items():
            fields.append({
                "name": key, "default": getattr(template, key),
                "unit": _UNITS[key],
                "description": field.description or key.replace("_", " "),
                "native_value_key": key,
                "validation": str(field.annotation),
            })
        groups.append({"group": name, "label": _DISPLAY[name], "fields": fields})
    return {
        "schema_version": "sat-native-initial-configuration.v1",
        "capability_id": "whole_spacecraft.unified_native.v1",
        "model_source": "sat_sim.bsk_engine.unified_native.UnifiedNativeRuntime",
        "graph_scope": "INTEGRATED_WHOLE_NATIVE_ONLY",
        "user_configurable": True,
        "configuration_time": "PREPARE_ONLY",
        "standalone_subsystem_continuous_simulation_supported": False,
        "arbitrary_component_types_supported": False,
        "experimental_full_native_resume_supported": False,
        "groups": groups,
    }
