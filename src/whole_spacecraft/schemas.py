"""Schemas for whole-spacecraft assembly and execution."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from subsystems.adcs.degradation import ADCSDegradation
from subsystems.eps.degradation import EPSDegradation
from subsystems.propulsion.degradation import PropulsionDegradation
from subsystems.propulsion.schemas import PropulsionConfig
from subsystems.thermal.degradation import ThermalDegradation


@dataclass(frozen=True)
class WholeSpacecraftCouplingConfig:
    """Schema-controlled cross-subsystem coupling switches.

    These fields control whether the whole-spacecraft builder creates the
    corresponding runtime link.  Capability evidence must come from the built
    graph/coupling matrix, not from a hand-written integration manifest.
    """

    enable_payload_to_comm_storage: bool = True
    enable_comm_transmitter_to_storage_drain: bool = True
    enable_comm_native_odh_downlink: bool = True
    enable_eps_loads_to_thermal_heat: bool = True
    enable_thermal_heaters_to_eps_load: bool = True
    enable_gravity_to_spacecraft: bool = True
    enable_orbit_eclipse_to_thermal_shadow: bool = True
    enable_orbit_sun_attitude_eclipse_to_eps_solar_power: bool = True
    enable_adcs_pointing_to_payload_comm_gate: bool = True
    enable_propulsion_effector_to_spacecraft: bool = True


@dataclass(frozen=True)
class WholeSpacecraftCouplingRecord:
    """Runtime evidence for one cross-subsystem coupling built by the builder."""

    name: str
    source_subsystem: str
    sink_subsystem: str
    coupling_status: str
    interface: str
    evidence: tuple[str, ...]
    built_by: str = "whole_spacecraft.builder"
    notes: tuple[str, ...] = ()


@dataclass(frozen=True)
class WholeSpacecraftConfig:
    """Whole-spacecraft builder configuration."""

    adcs_dyn_step_s: float = 0.2
    adcs_fsw_step_s: float = 0.2
    mission_initial_sigma_bn: tuple[float, float, float] = (0.0, 0.0, 0.0)
    mission_initial_omega_bn_b_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    initial_orbit_radius_m: float = 7_000_000.0
    initial_orbit_phase_deg: float = 0.0

    battery_capacity_wh: float = 160.0
    initial_soc: float = 0.62
    solar_power_w: float = 95.0
    solar_panel_normal_b: tuple[float, float, float] = (1.0, 0.0, 0.0)
    payload_power_w: float = 38.0
    bus_power_w: float = 18.0

    instrument_baud_bps: float = 2.5e6
    payload_data_name: str = "payload_science"
    payload_model_tag: str = "wscPayloadInstrument"

    storage_capacity_bits: float = 6.0e9
    storage_initial_bits: float = 0.0
    transmitter_baud_bps: float = 1.5e6
    native_downlink_bit_rate_request_bps: float = 1.5e6
    comm_power_reference_rate_bps: float = 1.5e6
    native_downlink_packet_size_bits: float = 1000.0
    native_downlink_max_retransmissions: int = 1
    native_downlink_cnr_linear: float = 1.0e9
    native_downlink_cnr_floor_linear: float = 0.0
    native_downlink_distance_m: float = 500_000.0
    native_downlink_bandwidth_hz: float = 1.0e6
    native_downlink_frequency_hz: float = 2.2e9
    spacecraft_antenna_orientation_b: tuple[float, float, float] = (0.0, -0.41421356237309503, 0.0)
    native_link_budget_pointing_loss_enabled: bool = True
    native_link_budget_frequency_loss_enabled: bool = True
    native_link_budget_atmospheric_attenuation_enabled: bool = False
    storage_model_tag: str = "wscStorageUnit"
    transmitter_model_tag: str = "wscTransmitter"

    thermal_step_s: float = 10.0
    recorder_step_s: float = 10.0
    thermal_heat_power_w: float = 30.0
    thermal_use_network: bool = True

    propulsion_enabled: bool = True
    propulsion_config: PropulsionConfig | None = None
    propulsion_on_time_s: tuple[float, ...] | None = None

    orb_env_step_s: float = 1.0
    orb_env_sun_model: str = "fixed"
    orb_env_sun_vector_n: tuple[float, float, float] = (1.0, 0.0, 0.0)
    orb_env_magnetic_field_model: str = "dipole"
    orb_env_use_j2_gravity: bool = True
    orb_env_spice_data_path: str | None = None
    orb_env_wmm_data_path: str | None = None
    orb_env_strict_resource_loading: bool = False
    orb_env_spice_epoch_utc: str = "2025 JAN 01 00:00:00.000"
    orb_env_enable_eclipse: bool = True

    eps_degradation: EPSDegradation | None = None
    adcs_degradation: ADCSDegradation | None = None
    propulsion_degradation: PropulsionDegradation | None = None
    thermal_degradation: ThermalDegradation | None = None
    payload_degradation: Any | None = None
    comm_data_degradation: Any | None = None

    coupling: WholeSpacecraftCouplingConfig = field(default_factory=WholeSpacecraftCouplingConfig)
    fault_specs: list[Any] = field(default_factory=list)
    apply_fault_specs_at_build_time: bool = False

    # Parameter-provenance gate.  Default demo parameters may run, but reports
    # must not claim engineering, ground-calibrated or flight-correlated
    # qualification unless the registry satisfies the requested confidence
    # profile.
    parameter_profile: str = "demo"
    parameter_registry_path: str | None = None
    parameter_minimum_confidence: str = "demo"
    strict_parameter_provenance: bool = True


@dataclass(frozen=True)
class WholeSpacecraftGraph:
    """Container for the assembled whole-spacecraft Basilisk graph."""

    sim: Any
    sim_task_name: str
    fsw_task_name: str
    orb_env_task_name: str
    thermal_task_name: str
    recorder_task_name: str
    spacecraft: Any
    adcs_graph: Any
    eps_graph: Any
    payload_graph: Any
    comm_graph: Any
    thermal_graph: Any
    propulsion_graph: Any | None
    orb_env_graph: Any
    battery: Any
    storage: Any
    instrument: Any
    transmitter: Any
    fuel_tank: Any | None
    mission_gate: Any | None = None
    component_registry: dict[str, Any] = field(default_factory=dict)
    fault_injector: Any | None = None
    applied_modes: dict[str, Any] = field(default_factory=dict)
    recorders: dict[str, Any] = field(default_factory=dict)
    coupling_matrix: dict[str, WholeSpacecraftCouplingRecord] = field(default_factory=dict)
    integration_manifest: dict[str, Any] = field(default_factory=dict)
    task_cadence_probes: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class WholeSpacecraftMissionGateTraceRow:
    time_s: float
    payload_enabled: bool
    has_access: bool
    thermal_safe: bool
    battery_soc: float
    attitude_error_norm: float
    attitude_error_deg: float
    payload_generated_bps: float
    downlink_requested_rate_bps: float
    downlink_rate_bps: float
    payload_power_available: bool = True
    comm_power_available: bool = True


@dataclass(frozen=True)
class WholeSpacecraftRunConfig:
    """Whole-spacecraft runner configuration."""

    duration_s: float = 300.0
    sample_s: float = 10.0
    access_window_s: float = 20.0
    access_period_s: float = 60.0
    max_pointing_error_deg: float = 0.25
    structure: WholeSpacecraftConfig | None = None


@dataclass(frozen=True)
class WholeSpacecraftTraceRow:
    """One sample row of the whole-spacecraft run.

    ``downlink_rate_bps`` is the successfully delivered native downlink rate.
    The requested/gated rate is reported separately so a blocked or lossy link
    cannot be mistaken for successful data return.
    """

    time_s: float
    battery_storage_j: float
    battery_soc: float
    net_power_w: float
    battery_temp_c: float
    shunt_dissipated_w: float
    data_storage_bits: float
    instrument_baud_bps: float
    transmitter_baud_bps: float
    thermal_temp_c: float
    thermal_margin_c: float
    thermal_safe: bool
    attitude_error_norm: float
    attitude_error_deg: float
    rate_error_norm_rad_s: float
    payload_enabled: bool
    payload_generated_bps: float
    downlink_requested_rate_bps: float
    downlink_rate_bps: float
    downlink_attempted_bps: float
    downlink_delivered_bps: float
    downlink_dropped_bps: float
    downlink_storage_removal_bps: float
    downlink_ber: float
    downlink_per: float
    cumulative_delivered_bits: float
    cumulative_dropped_bits: float
    cumulative_removed_bits: float
    power_bus_available: bool
    propellant_remaining_kg: float
    fuel_mass_flow_kg_s: float
    tank_pressure_pa: float
    tank_pressure_available: bool
    adcs_control_power_w: float = 0.0
    propulsion_power_w: float = 0.0
    propulsion_activity: float = 0.0
    propulsion_cumulative_energy_j: float = 0.0
    heater_eps_load_w: float = 0.0
    heater_active_count: float = 0.0
    pdu_heater_enabled: float = 0.0
    eclipse_shadow_factor: float = 1.0
    ground_access: float = 0.0
    ground_slant_range_m: float = 0.0
    rf_link_distance_m: float = 0.0
    rf_cnr_linear: float = 0.0
    orbit_radius_m: float = 0.0
    orbit_speed_m_s: float = 0.0
    spacecraft_position_x_m: float = 0.0
    thermal_adcs_temp_c: float = 0.0
    thermal_structure_temp_c: float = 0.0
    thermal_solar_panel_temp_c: float = 0.0
    gravity_coupling_enabled: float = 1.0
    propulsion_effector_coupling_enabled: float = 1.0
    propellant_used_kg: float = 0.0


@dataclass(frozen=True)
class WholeSpacecraftSummary:
    """Summary of a whole-spacecraft run.

    ``status`` is the overall run verdict and is PASS only when both
    ``execution_status`` and ``mission_status`` pass.  Explicit status channels
    remain available so structural, numerical, injection, physics and mission
    failures can be diagnosed without overloading one field.
    """

    backend: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    unified_simbase: bool
    native_modules: tuple[str, ...]
    scheduled_custom_modules: tuple[str, ...]
    included_subsystems: tuple[str, ...]
    excluded_subsystems: tuple[str, ...]
    duration_s: float
    step_s: float
    dynamics_step_s: float
    fsw_step_s: float
    orb_env_step_s: float
    thermal_step_s: float
    recorder_step_s: float
    step_semantics: str
    sample_count: int
    final_soc: float
    min_soc: float
    final_data_storage_bits: float
    min_thermal_margin_c: float
    final_propellant_kg: float
    min_propellant_kg: float
    status: str
    not_claimed: tuple[str, ...]
    execution_status: str = "UNKNOWN"
    structural_status: str = "UNKNOWN"
    runtime_injection_status: str = "UNKNOWN"
    numerical_status: str = "UNKNOWN"
    physics_status: str = "UNKNOWN"
    coupling_causality_status: str = "NOT_EVALUATED_PAIRED_RUN_REQUIRED"
    physics_claim_scope: str = "single_run_wiring_numerics_and_conservation_only"
    mission_status: str = "UNKNOWN"
    calibration_status: str = "demo_unqualified"
    parameter_profile: str = "demo"
    parameter_registry_status: str = "UNKNOWN"
    parameter_minimum_confidence: str = "demo"
    parameter_registry_record_count: int = 0
    assumed_parameter_count: int = 0
    uncalibrated_parameter_count: int = 0
    extrapolated_parameter_count: int = 0
    missing_parameter_count: int = 0
    invalid_parameter_count: int = 0
    parameter_registry_path: str = ""
    mission_payload_enabled_count: int = 0
    mission_access_count: int = 0
    mission_geometric_access_count: int = 0
    mission_generated_bits: float = 0.0
    mission_delivered_bits: float = 0.0
    mission_dropped_bits: float = 0.0
    max_downlink_delivered_bps: float = 0.0
    max_downlink_dropped_bps: float = 0.0
    final_attitude_error_deg: float = 0.0
    runtime_unsupported_count: int = 0
    runtime_fault_event_status: str = "not_applicable"
    runtime_fault_event_count: int = 0
    runtime_fault_triggered_count: int = 0
    runtime_fault_fallback_count: int = 0
    runtime_fault_mutation_target_count: int = 0
    runtime_fault_recovery_expected_count: int = 0
    runtime_fault_recovery_registered_count: int = 0
    runtime_fault_recovery_triggered_count: int = 0
    runtime_fault_recovery_failure_count: int = 0
    coupling_count: int = 0
    active_coupling_count: int = 0
    native_coupling_count: int = 0
    proxy_coupling_count: int = 0
    disabled_coupling_count: int = 0
    coupling_runtime_catalog: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    mission_success_score: float = 0.0
    mission_success_score_basis: str = "deterministic_gate_fraction_not_probability"
    battery_storage_delta_j: float = 0.0
    integrated_net_power_j: float = 0.0
    energy_balance_residual_j: float = 0.0
    energy_balance_relative_error: float = 0.0
    energy_balance_tolerance_j: float = 0.0
    energy_conservation_status: str = "UNKNOWN"
    data_storage_delta_bits: float = 0.0
    integrated_payload_generated_bits: float = 0.0
    data_balance_removed_bits: float = 0.0
    data_balance_residual_bits: float = 0.0
    data_balance_relative_error: float = 0.0
    data_balance_tolerance_bits: float = 0.0
    data_conservation_status: str = "UNKNOWN"
    eps_to_thermal_electrical_energy_j: float = 0.0
    eps_to_thermal_heat_energy_j: float = 0.0
    heater_electrical_energy_j: float = 0.0
    heater_thermal_energy_j: float = 0.0
    thermal_power_bridge_count: int = 0
    thermal_power_bridge_exercised_count: int = 0
    proxy_coupling_runtime_status: str = "UNKNOWN"
    solar_power_coupling_status: str = "UNKNOWN"
    solar_power_sample_count: int = 0
    min_solar_power_w: float = 0.0
    max_solar_power_w: float = 0.0
    native_rf_cnr_max_linear: float = 0.0
    gated_downlink_cnr_max_linear: float = 0.0
    rf_environment_wiring_count: int = 0
    runtime_task_cadence_status: str = "UNKNOWN"
    runtime_task_cadence: dict[str, Any] = field(default_factory=dict)


__all__ = [
    "WholeSpacecraftCouplingConfig",
    "WholeSpacecraftCouplingRecord",
    "WholeSpacecraftConfig",
    "WholeSpacecraftGraph",
    "WholeSpacecraftMissionGateTraceRow",
    "WholeSpacecraftRunConfig",
    "WholeSpacecraftTraceRow",
    "WholeSpacecraftSummary",
]
