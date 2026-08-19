"""Communication and data-handling subsystem schemas.

The v3.5 Comm/Data subsystem is a pure-Python subsystem composition of:

    data generation -> storage queue -> ground access -> link budget -> downlink

It intentionally does not import Basilisk.  A later bridge can map access and
communication modules to Basilisk messages.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping

from components.data_queue.builder import DataQueueConfig, DataQueueState
from components.link_budget.builder import LinkBudgetConfig
from components.ground_station.builder import GroundStationConfig
from components.transmitter.builder import TransmitterConfig


@dataclass(frozen=True)
class CommDataConfig:
    """Communication/data-handling subsystem configuration.

    Attributes:
        queue: Storage queue configuration.
        link: Link budget configuration used when access exists.
        ground_station: Ground access configuration.
        station_pos_ecef_m: Fixed station position for subsystem harnesses.
        generated_bps_by_mode: Mode-dependent onboard data generation.
        default_generated_bps: Fallback data generation rate.
        max_downlink_bps: Optional hard cap on downlink rate after link budget.
        require_access_for_downlink: If true, no downlink occurs without ground access.
        require_eps_permission: If true, EPS permission is required for downlink.
    """

    queue: DataQueueConfig = field(default_factory=DataQueueConfig)
    link: LinkBudgetConfig = field(default_factory=LinkBudgetConfig)
    ground_station: GroundStationConfig = field(default_factory=GroundStationConfig)
    transmitter: TransmitterConfig = field(default_factory=TransmitterConfig)
    station_pos_ecef_m: tuple[float, float, float] = (6_378_000.0, 0.0, 0.0)
    generated_bps_by_mode: Mapping[str, float] = field(default_factory=dict)
    default_generated_bps: float = 0.0
    max_downlink_bps: float | None = None
    require_access_for_downlink: bool = True
    require_eps_permission: bool = True


@dataclass(frozen=True)
class CommDataState:
    """Communication/data dynamic state."""

    queue: DataQueueState
    time_s: float = 0.0


@dataclass(frozen=True)
class CommDataStepInput:
    """One communication/data propagation input sample."""

    dt_s: float
    mode: str = "nominal"
    spacecraft_pos_ecef_m: tuple[float, float, float] = (6_878_000.0, 0.0, 0.0)
    generated_bps: float | None = None
    slant_range_m: float | None = None
    access_override: bool | None = None
    eps_allows_downlink: bool = True
    downlink_requested: bool = True


@dataclass(frozen=True)
class CommDataStepResult:
    """One communication/data propagation output sample."""

    time_s: float
    mode: str
    generated_bps: float
    generated_bits: float
    has_access: bool
    elevation_deg: float
    slant_range_m: float
    link_effective_rate_bps: float
    downlink_rate_bps: float
    downlinked_bits_step: float
    dropped_bits_step: float
    queue_bits: float
    cumulative_downlinked_bits: float
    cumulative_dropped_bits: float
    eps_allows_downlink: bool
    downlink_requested: bool
    ebn0_db: float
    tx_power_w: float
    tx_power_draw_w: float
    tx_efficiency: float


@dataclass(frozen=True)
class CommDataProfileResult:
    """Multi-step communication/data profile."""

    time_s: tuple[float, ...]
    queue_bits: tuple[float, ...]
    generated_bits: tuple[float, ...]
    downlinked_bits_step: tuple[float, ...]
    dropped_bits_step: tuple[float, ...]
    cumulative_downlinked_bits: tuple[float, ...]
    cumulative_dropped_bits: tuple[float, ...]
    has_access: tuple[bool, ...]
    downlink_rate_bps: tuple[float, ...]
    link_effective_rate_bps: tuple[float, ...]
    ebn0_db: tuple[float, ...]



@dataclass(frozen=True)
class CommDataBasiliskConfig:
    """Basilisk-backed Comm/Data run configuration.

    The dataclass is schema-only and intentionally does not import Basilisk.
    """

    duration_s: float = 300.0
    step_s: float = 10.0
    instrument_baud_bps: float = 2.0e6
    storage_capacity_bits: float = 5.0e9
    transmitter_baud_bps: float = 1.0e6
    data_name: str = "payload_science"
    initial_storage_bits: float = 0.0
    native_storage_drain_enabled: bool = False


@dataclass(frozen=True)
class CommDataBasiliskTraceRow:
    time_s: float
    instrument_baud_bps: float
    storage_level_bits: float
    storage_capacity_bits: float
    transmitter_baud_bps: float
    native_storage_drain_enabled: bool = False
    transmitter_storage_node_baud_bps: float = 0.0


@dataclass(frozen=True)
class CommDataBasiliskSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    duration_s: float
    step_s: float
    sample_count: int
    final_storage_bits: float
    max_storage_bits: float
    min_storage_bits: float
    nominal_generated_bits: float
    estimated_native_downlinked_bits: float
    native_storage_drain_enabled: bool
    status: str


# Backward-compatible names for callers that previously imported from the
# removed legacy Basilisk compatibility module.
CommDataNativeConfig = CommDataBasiliskConfig
CommDataNativeTraceRow = CommDataBasiliskTraceRow
CommDataNativeSummary = CommDataBasiliskSummary


__all__ = [
    "CommDataConfig",
    "CommDataState",
    "CommDataStepInput",
    "CommDataStepResult",
    "CommDataProfileResult",

    "CommDataBasiliskConfig",
    "CommDataBasiliskTraceRow",
    "CommDataBasiliskSummary",
    "CommDataNativeConfig",
    "CommDataNativeTraceRow",
    "CommDataNativeSummary",
    "CommDataRfNativeConfig",
    "CommDataRfNativeTraceRow",
    "CommDataRfNativeSummary",
    "CommDataOdhNativeConfig",
    "CommDataOdhNativeTraceRow",
    "CommDataOdhNativeSummary",
]


@dataclass(frozen=True)
class CommDataRfNativeConfig:
    """Basilisk native RF-link run configuration for COMMDATA-RF-NATIVE-1."""

    duration_s: float = 2.0
    step_s: float = 1.0
    spacecraft_radius_m: float = 7_000_000.0
    ground_radius_m: float = 6_378_000.0
    frequency_hz: float = 2.2e9
    bandwidth_hz: float = 1.0e6
    spacecraft_tx_power_w: float = 5.0
    ground_tx_power_w: float = 1.0e-5
    spacecraft_rx_power_w: float = 1.0e-5
    ground_rx_power_w: float = 1.0
    spacecraft_directivity_db: float = 20.0
    ground_directivity_db: float = 20.0
    hpbw_ratio: float = 1.0
    radiation_efficiency: float = 0.55
    equivalent_noise_temp_k: float = 290.0
    environment_temp_k: float = 290.0
    spacecraft_available: bool = True
    ground_available: bool = True
    pointing_loss_enabled: bool = False
    frequency_loss_enabled: bool = True
    atmospheric_attenuation_enabled: bool = False


@dataclass(frozen=True)
class CommDataRfNativeTraceRow:
    time_s: float
    distance_m: float
    frequency_hz: float
    bandwidth_hz: float
    cnr1: float
    cnr2: float
    spacecraft_eirp_db: float
    spacecraft_tx_power_w: float
    ground_rx_power_w: float
    spacecraft_state: int
    ground_state: int


@dataclass(frozen=True)
class CommDataRfNativeSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    duration_s: float
    step_s: float
    sample_count: int
    distance_m: float
    frequency_hz: float
    bandwidth_hz: float
    cnr1_final: float
    cnr2_final: float
    spacecraft_eirp_db_final: float
    native_fspl_db: float
    spacecraft_state_final: int
    ground_state_final: int
    status: str


@dataclass(frozen=True)
class CommDataOdhNativeConfig:
    """Native ODH/downlink handling configuration for COMMDATA-ODH-NATIVE-1.

    The focused harness uses Basilisk ``SimpleInstrument`` and
    ``SimpleStorageUnit`` for data generation/storage and Basilisk
    ``DownlinkHandling`` for BER/PER, delivered/dropped data rate and storage
    removal.  ``link_active``/``cnr`` are supplied through a Basilisk
    ``LinkBudgetMsg`` so the ODH module, not a project proxy, computes packet
    success and removal rates.
    """

    duration_s: float = 20.0
    step_s: float = 1.0
    instrument_baud_bps: float = 1.0e6
    storage_capacity_bits: float = 1.0e9
    initial_storage_bits: float = 0.0
    bit_rate_request_bps: float = 2.0e6
    packet_size_bits: float = 1000.0
    max_retransmissions: int = 1
    receiver_index: int = 2
    require_full_packet: bool = False
    remove_delivered_only: bool = True
    data_name: str = "payload_science"
    link_active: bool = True
    cnr_linear: float = 1.0e9
    link_distance_m: float = 500_000.0
    link_bandwidth_hz: float = 1.0e6
    link_frequency_hz: float = 2.2e9


@dataclass(frozen=True)
class CommDataOdhNativeTraceRow:
    time_s: float
    storage_level_bits: float
    storage_capacity_bits: float
    storage_net_baud_bps: float
    instrument_baud_bps: float
    downlink_node_baud_bps: float
    link_active: bool
    bit_rate_request_bps: float
    attempted_data_rate_bps: float
    delivered_data_rate_bps: float
    dropped_data_rate_bps: float
    storage_removal_rate_bps: float
    cumulative_delivered_bits: float
    cumulative_dropped_bits: float
    cumulative_removed_bits: float
    ber: float
    per: float
    packet_success_prob: float
    packet_drop_prob: float
    ebn0_db: float


@dataclass(frozen=True)
class CommDataOdhNativeSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    native_modules: tuple[str, ...]
    message_contracts: tuple[str, ...]
    duration_s: float
    step_s: float
    sample_count: int
    final_storage_bits: float
    max_storage_bits: float
    min_storage_bits: float
    delivered_bits: float
    dropped_bits: float
    removed_bits: float
    final_ber: float
    final_per: float
    final_packet_success_prob: float
    link_active: bool
    status: str
