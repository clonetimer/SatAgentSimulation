"""EPS subsystem schemas.

The EPS subsystem is a pure-Python subsystem-level composition of component
models.  It intentionally does not import Basilisk.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from components.battery import BatteryConfig, BatteryState
from components.solar_panel.builder import SolarPanelConfig, SolarPanelState
from components.pdu.builder import PduConfig
from components.power_sink.builder import PowerSinkConfig


@dataclass(frozen=True)
class EpsConfig:
    """Electrical Power Subsystem configuration.

    Attributes:
        battery: Battery single-unit configuration.
        solar_panel: Solar panel single-unit configuration.
        pdu: Power distribution unit configuration.
        loads: Named load definitions.  Each load can provide mode-specific
            demand through ``PowerSinkConfig.mode_power_w``.
        initial_panel_normal_b: Initial solar panel normal in body frame.
        default_sun_direction_b: Default sun direction in body frame.
        low_soc_threshold: Optional threshold below which configured loads are
            pre-shed before PDU bus-limit shedding.  Set <=0 to disable.
        low_soc_shed_loads: Loads disabled when SOC is below threshold.
    """

    battery: BatteryConfig = field(default_factory=BatteryConfig)
    solar_panel: SolarPanelConfig = field(default_factory=SolarPanelConfig)
    pdu: PduConfig = field(default_factory=PduConfig)
    loads: Mapping[str, PowerSinkConfig] = field(default_factory=dict)
    initial_panel_normal_b: tuple[float, float, float] = (1.0, 0.0, 0.0)
    default_sun_direction_b: tuple[float, float, float] = (1.0, 0.0, 0.0)
    low_soc_threshold: float = 0.0
    low_soc_shed_loads: tuple[str, ...] = ()


@dataclass(frozen=True)
class EpsState:
    """EPS dynamic state."""

    battery: BatteryState
    solar_panel: SolarPanelState
    time_s: float = 0.0


@dataclass(frozen=True)
class EpsStepInput:
    """One EPS propagation input sample."""

    dt_s: float
    mode: str = "nominal"
    shadow_factor: float = 1.0
    sun_direction_b: tuple[float, float, float] | None = None
    requested_loads_w: Mapping[str, float] = field(default_factory=dict)
    battery_temp_c: float | None = None


@dataclass(frozen=True)
class EpsStepResult:
    """One EPS propagation output sample."""

    time_s: float
    mode: str
    solar_power_w: float
    load_requested_w: float
    load_served_w: float
    net_power_w: float
    battery_soc: float
    battery_storage_wh: float
    shunt_dissipated_w: float
    battery_temp_c: float | None
    shed_loads: tuple[str, ...]
    low_soc_shed_loads: tuple[str, ...]
    overload_remaining: bool
    load_request_by_name_w: dict[str, float]
    load_served_by_name_w: dict[str, float]


@dataclass(frozen=True)
class EpsProfileResult:
    """Multi-step EPS history."""

    time_s: tuple[float, ...]
    solar_power_w: tuple[float, ...]
    load_requested_w: tuple[float, ...]
    load_served_w: tuple[float, ...]
    net_power_w: tuple[float, ...]
    battery_soc: tuple[float, ...]
    battery_storage_wh: tuple[float, ...]
    shunt_dissipated_w: tuple[float, ...]
    battery_temp_c: tuple[float | None, ...]
    shed_loads: tuple[tuple[str, ...], ...]
    low_soc_shed_loads: tuple[tuple[str, ...], ...]
    overload_remaining: tuple[bool, ...]



@dataclass(frozen=True)
class EPSBasiliskConfig:
    """Basilisk-backed EPS/PDU run configuration.

    The dataclass is schema-only and intentionally does not import Basilisk.
    """

    duration_s: float = 300.0
    step_s: float = 10.0
    battery_capacity_wh: float = 120.0
    initial_soc: float = 0.50
    solar_power_w: float = 80.0
    solar_efficiency: float = 0.25
    solar_normal_b: tuple[float, float, float] = (1.0, 0.0, 0.0)
    use_simple_solar_panel: bool = False
    solar_panel_enabled: bool = True
    solar_panel_area_m2: float | None = None
    battery_fault_capacity_ratio: float | None = None
    rw_power_w: float = 0.0
    antenna_power_w: float = 0.0
    bus_power_w: float = 12.0
    payload_power_w: float = 35.0
    adcs_power_w: float = 0.0
    comm_power_w: float = 0.0
    heater_power_w: float = 0.0
    payload_requested: bool = True
    adcs_requested: bool = True
    comm_requested: bool = True
    heater_requested: bool = True
    payload_min_soc: float = 0.55
    comm_min_soc: float = 0.50
    heater_min_soc: float = 0.30
    adcs_min_soc: float = 0.20
    recovery_soc: float = 0.65


@dataclass(frozen=True)
class EPSBasiliskTraceRow:
    time_s: float
    battery_storage_j: float
    battery_capacity_j: float
    battery_soc: float
    solar_power_w: float
    bus_load_w: float
    payload_load_requested_w: float
    payload_load_enabled_w: float
    adcs_load_requested_w: float
    adcs_load_enabled_w: float
    comm_load_requested_w: float
    comm_load_enabled_w: float
    heater_load_requested_w: float
    heater_load_enabled_w: float
    rw_power_w: float
    antenna_power_w: float
    battery_fault_capacity_ratio: float | None
    solar_panel_enabled: bool
    payload_enabled: bool
    adcs_enabled: bool
    comm_enabled: bool
    heater_enabled: bool
    load_shed_active: bool
    shed_reason: str
    net_power_w: float

    @property
    def payload_power_w(self) -> float:
        return self.payload_load_enabled_w


@dataclass(frozen=True)
class EPSBasiliskSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    custom_modules: tuple[str, ...]
    message_contracts: tuple[str, ...]
    duration_s: float
    step_s: float
    sample_count: int
    initial_storage_j: float
    final_storage_j: float
    expected_final_storage_j: float
    final_soc: float
    load_shed_event_count: int
    status: str
    not_claimed: tuple[str, ...]


# Backward-compatible names for callers that previously imported from the
# removed legacy Basilisk compatibility module.
EPSNativeConfig = EPSBasiliskConfig
EPSNativeTraceRow = EPSBasiliskTraceRow
EPSNativeSummary = EPSBasiliskSummary


__all__ = [
    "EpsConfig",
    "EpsState",
    "EpsStepInput",
    "EpsStepResult",
    "EpsProfileResult",

    "EPSBasiliskConfig",
    "EPSBasiliskTraceRow",
    "EPSBasiliskSummary",
    "EPSNativeConfig",
    "EPSNativeTraceRow",
    "EPSNativeSummary",
]
