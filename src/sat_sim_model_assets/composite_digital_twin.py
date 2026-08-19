"""Governed model assets for the A4R composite digital twin migration."""
from __future__ import annotations

from sat_sim_kernel import (
    BindingSet,
    ConnectionKind,
    DataType,
    EffectBinding,
    EffectKind,
    EffectSpec,
    EvidenceBinding,
    EvidenceSpec,
    ImplementationId,
    LocalId,
    ModelConnection,
    ModelDefinition,
    ModelGraphDefinition,
    ModelImplementationProfile,
    ModelKind,
    ModelNode,
    ModelPath,
    ModelRef,
    ObjectRef,
    ParameterSpec,
    PortDirection,
    PortEndpoint,
    PortSpec,
    PropertyBinding,
)
from sat_sim_operations import ObjectEvidenceSpec, ObjectPropertySpec, OperationsObjectDefinition
from sat_sim_projection import CapabilityProjection, ExposedParameter

from .contracts import ModelAssetRegistry, ParameterSet


def lid(value: str) -> LocalId:
    return LocalId(value)


def mref(value: str) -> ModelRef:
    return ModelRef.parse(value)


COMPOSITE_CAPABILITY_ID = "whole_spacecraft.composite_digital_twin.v1"
COMPOSITE_ADAPTER_KEY = "basilisk.composite_digital_twin_graph"
COMPOSITE_SPACECRAFT_REF = mref("spacecraft.composite_digital_twin@1")
ORBIT_ENVIRONMENT_REF = mref("subsystem.orbit_environment_composite@1")
ADCS_REF = mref("subsystem.adcs_composite@1")
EPS_REF = mref("subsystem.eps_composite@1")
THERMAL_REF = mref("subsystem.thermal_composite@1")
COMM_DATA_REF = mref("subsystem.comm_data_composite@1")
PAYLOAD_REF = mref("subsystem.payload_composite@1")
PROPULSION_REF = mref("subsystem.propulsion_composite@1")
DATA_STORAGE_REF = mref("component.data_storage_composite@1")
SPACECRAFT_DYNAMICS_REF = mref("component.spacecraft_dynamics_composite@1")
COMPOSITE_IMPLEMENTATION = ImplementationId.parse("spacecraft.composite_digital_twin.basilisk@1")
COMPOSITE_PARAMETER_SET_ID = lid("composite-digital-twin-demo-v1")
COMPOSITE_BINDING_SET_ID = lid("composite-digital-twin-bindings-v1")
COMPOSITE_PROJECTION_ID = lid("composite-digital-twin-projection-v1")
COMPOSITE_OBJECT_REF = ObjectRef("spacecraft.composite_digital_twin")

FAULT_EFFECT_IDS = (
    "eps_battery_capacity_loss",
    "eps_battery_open_circuit",
    "adcs_rw_jamming",
    "adcs_rw_bearing_seizure",
    "propulsion_thruster_ignition_failure",
    "payload_instrument_off",
    "comm_data_downlink_link_loss",
    "comm_data_storage_capacity_loss",
    "thermal_heater_stuck_off",
    "thermal_heater_stuck_on",
    "thermal_radiator_rejection_loss",
)

DEGRADATION_EFFECT_IDS = (
    "battery_capacity_loss_30pct",
    "solar_panel_efficiency_loss_20pct",
    "thruster_thrust_loss_15pct",
    "combined_degradation",
    "adcs_rw_friction_and_sensor_noise",
    "thermal_heater_and_radiator_degradation",
    "fuel_leak_and_pressure_loss",
    "multi_subsystem_end_of_life",
)

EFFECT_IDS = ("nominal", *FAULT_EFFECT_IDS, *DEGRADATION_EFFECT_IDS)

REQUIRED_RUNTIME_COUPLINGS = (
    "payload_to_comm_storage",
    "payload_activity_to_eps_thermal",
    "orbit_sun_attitude_eclipse_to_eps_solar_power",
    "comm_native_odh_to_storage_drain",
    "eps_pdu_to_payload_activity",
    "adcs_pointing_to_payload_comm_gate",
    "comm_activity_to_eps_thermal",
    "eps_pdu_to_comm_activity",
    "eps_loads_to_thermal_heat",
    "comm_transmitter_to_storage_drain",
    "adcs_control_effort_to_eps_thermal",
    "adcs_spacecraft_to_orbit_environment",
    "eps_pdu_to_thermal_heaters",
    "gravity_to_spacecraft",
    "ground_access_rf_link_budget",
    "orbit_eclipse_to_thermal_shadow",
    "propulsion_activity_to_eps_thermal",
    "propulsion_effector_to_spacecraft",
    "thermal_heaters_to_eps_load",
)


def _param(
    name: str,
    data_type: DataType,
    *,
    unit: str | None = None,
    default: object | None = None,
    default_is_set: bool = True,
    minimum: float | None = None,
    maximum: float | None = None,
    description: str = "",
) -> ParameterSpec:
    return ParameterSpec(
        lid(name),
        data_type,
        unit=unit,
        default_is_set=default_is_set,
        default=default,
        minimum=minimum,
        maximum=maximum,
        description=description,
    )


def _in(name: str, data_type: DataType, unit: str | None = None) -> PortSpec:
    return PortSpec(lid(name), PortDirection.INPUT, data_type, unit=unit)


def _out(name: str, data_type: DataType, unit: str | None = None) -> PortSpec:
    return PortSpec(lid(name), PortDirection.OUTPUT, data_type, unit=unit)


COMPOSITE_PARAMETERS = (
    _param("adcs_dyn_step_s", DataType.NUMBER, unit="s", default=0.2, minimum=1.0e-6),
    _param("adcs_fsw_step_s", DataType.NUMBER, unit="s", default=0.2, minimum=1.0e-6),
    _param("thermal_step_s", DataType.NUMBER, unit="s", default=10.0, minimum=1.0e-6),
    _param("battery_capacity_wh", DataType.NUMBER, unit="Wh", default=160.0, minimum=1.0e-6),
    _param("initial_soc", DataType.NUMBER, unit="ratio", default=0.62, minimum=0.0, maximum=1.0),
    _param("solar_power_w", DataType.NUMBER, unit="W", default=95.0, minimum=0.0),
    _param("payload_power_w", DataType.NUMBER, unit="W", default=38.0, minimum=0.0),
    _param("bus_power_w", DataType.NUMBER, unit="W", default=18.0, minimum=0.0),
    _param("instrument_baud_bps", DataType.NUMBER, unit="bit/s", default=2_500_000.0, minimum=0.0),
    _param("storage_capacity_bits", DataType.NUMBER, unit="bit", default=6_000_000_000.0, minimum=1.0e-6),
    _param("storage_initial_bits", DataType.NUMBER, unit="bit", default=0.0, minimum=0.0),
    _param("transmitter_baud_bps", DataType.NUMBER, unit="bit/s", default=1_500_000.0, minimum=0.0),
    _param("native_downlink_bit_rate_request_bps", DataType.NUMBER, unit="bit/s", default=1_500_000.0, minimum=0.0),
    _param("comm_power_reference_rate_bps", DataType.NUMBER, unit="bit/s", default=1_500_000.0, minimum=1.0e-6),
    _param("native_downlink_packet_size_bits", DataType.NUMBER, unit="bit", default=1000.0, minimum=1.0),
    _param("native_downlink_max_retransmissions", DataType.INTEGER, default=1, minimum=0.0),
    _param("native_downlink_cnr_linear", DataType.NUMBER, unit="ratio", default=1.0e9, minimum=0.0),
    _param("native_downlink_distance_m", DataType.NUMBER, unit="m", default=500_000.0, minimum=0.0),
    _param("native_downlink_bandwidth_hz", DataType.NUMBER, unit="Hz", default=1.0e6, minimum=1.0e-6),
    _param("native_downlink_frequency_hz", DataType.NUMBER, unit="Hz", default=2.2e9, minimum=1.0e-6),
    _param("thermal_heat_power_w", DataType.NUMBER, unit="W", default=30.0, minimum=0.0),
    _param("thermal_use_network", DataType.BOOLEAN, default=True),
    _param("propulsion_enabled", DataType.BOOLEAN, default=True),
    _param("propulsion_on_time_s", DataType.ARRAY, unit="s", default_is_set=False),
    _param("access_window_s", DataType.NUMBER, unit="s", default=20.0, minimum=0.0),
    _param("access_period_s", DataType.NUMBER, unit="s", default=60.0, minimum=1.0e-6),
    _param("max_pointing_error_deg", DataType.NUMBER, unit="deg", default=0.25, minimum=0.0),
    _param("mission_initial_sigma_bn", DataType.ARRAY, default=(0.0, 0.0, 0.0)),
    _param("mission_initial_omega_bn_b_rad_s", DataType.ARRAY, unit="rad/s", default=(0.0, 0.0, 0.0)),
    _param("initial_orbit_radius_m", DataType.NUMBER, unit="m", default=7_000_000.0, minimum=1.0),
    _param("initial_orbit_phase_deg", DataType.NUMBER, unit="deg", default=0.0),
    _param("orbit_environment", DataType.OBJECT, default={}),
    _param("coupling", DataType.OBJECT, default={}),
    _param("parameter_profile", DataType.STRING, default="demo"),
    _param("parameter_registry_path", DataType.STRING, default_is_set=False),
    _param("parameter_minimum_confidence", DataType.STRING, default="demo"),
    _param("strict_parameter_provenance", DataType.BOOLEAN, default=True),
)

COMPOSITE_DEFAULT_VALUES = {
    str(item.name): item.default
    for item in COMPOSITE_PARAMETERS
    if item.default_is_set
}

COMMON_EVIDENCE = (
    EvidenceSpec(lid("status-evidence"), ModelPath("summary.status"), DataType.STRING),
    EvidenceSpec(lid("battery-soc-evidence"), ModelPath("eps.battery_soc"), DataType.NUMBER, unit="ratio"),
    EvidenceSpec(lid("thermal-temp-evidence"), ModelPath("thermal.thermal_temp_c"), DataType.NUMBER, unit="degC"),
    EvidenceSpec(lid("attitude-error-evidence"), ModelPath("adcs.attitude_error_deg"), DataType.NUMBER, unit="deg"),
    EvidenceSpec(lid("payload-generated-evidence"), ModelPath("payload.payload_generated_bps"), DataType.NUMBER, unit="bit/s"),
    EvidenceSpec(lid("downlink-delivered-evidence"), ModelPath("comm.downlink_delivered_bps"), DataType.NUMBER, unit="bit/s"),
    EvidenceSpec(lid("propellant-evidence"), ModelPath("propulsion.propellant_remaining_kg"), DataType.NUMBER, unit="kg"),
    EvidenceSpec(lid("effect-active-evidence"), ModelPath("labels.effect_active_count"), DataType.NUMBER, unit="count"),
)


def _effect(effect_id: str) -> EffectSpec:
    if effect_id == "nominal":
        kind = EffectKind.MODE
        evidence = (lid("status-evidence"),)
    elif effect_id in FAULT_EFFECT_IDS:
        kind = EffectKind.FAULT
        evidence = (lid("effect-active-evidence"),)
    else:
        kind = EffectKind.DEGRADATION
        evidence = (lid("effect-active-evidence"),)
    return EffectSpec(
        lid(effect_id),
        kind,
        target_patterns=("whole_spacecraft", effect_id),
        evidence_ids=evidence,
    )


ORBIT_ENVIRONMENT_DEFINITION = ModelDefinition(
    ORBIT_ENVIRONMENT_REF,
    ModelKind.SUBSYSTEM,
    "Composite orbit and environment",
    inputs=(_in("spacecraft_position_x_m", DataType.NUMBER, "m"),),
    outputs=(
        _out("solar_power_available_w", DataType.NUMBER, "W"),
        _out("eclipse_shadow_factor", DataType.NUMBER, "ratio"),
        _out("ground_slant_range_m", DataType.NUMBER, "m"),
        _out("gravity_accel_m_s2", DataType.NUMBER, "m/s2"),
    ),
)

ADCS_DEFINITION = ModelDefinition(
    ADCS_REF,
    ModelKind.SUBSYSTEM,
    "Composite ADCS",
    inputs=(_in("spacecraft_state", DataType.NUMBER),),
    outputs=(
        _out("attitude_error_deg", DataType.NUMBER, "deg"),
        _out("pointing_gate", DataType.BOOLEAN),
        _out("adcs_control_power_w", DataType.NUMBER, "W"),
        _out("thermal_adcs_heat_w", DataType.NUMBER, "W"),
    ),
)

EPS_DEFINITION = ModelDefinition(
    EPS_REF,
    ModelKind.SUBSYSTEM,
    "Composite EPS and PDU",
    inputs=(
        _in("solar_power_w", DataType.NUMBER, "W"),
        _in("payload_power_w", DataType.NUMBER, "W"),
        _in("comm_power_w", DataType.NUMBER, "W"),
        _in("adcs_power_w", DataType.NUMBER, "W"),
        _in("propulsion_power_w", DataType.NUMBER, "W"),
        _in("heater_power_w", DataType.NUMBER, "W"),
    ),
    outputs=(
        _out("battery_soc", DataType.NUMBER, "ratio"),
        _out("net_power_w", DataType.NUMBER, "W"),
        _out("pdu_payload_enabled", DataType.BOOLEAN),
        _out("pdu_comm_enabled", DataType.BOOLEAN),
        _out("pdu_heater_enabled", DataType.BOOLEAN),
        _out("eps_heat_w", DataType.NUMBER, "W"),
    ),
)

PAYLOAD_DEFINITION = ModelDefinition(
    PAYLOAD_REF,
    ModelKind.SUBSYSTEM,
    "Composite payload gate and instrument",
    inputs=(
        _in("pdu_payload_enabled", DataType.BOOLEAN),
        _in("pointing_gate", DataType.BOOLEAN),
    ),
    outputs=(
        _out("payload_generated_bps", DataType.NUMBER, "bit/s"),
        _out("payload_power_w", DataType.NUMBER, "W"),
        _out("payload_heat_w", DataType.NUMBER, "W"),
    ),
)

DATA_STORAGE_DEFINITION = ModelDefinition(
    DATA_STORAGE_REF,
    ModelKind.COMPONENT,
    "Composite onboard data storage",
    inputs=(
        _in("generated_bps", DataType.NUMBER, "bit/s"),
        _in("removed_bps", DataType.NUMBER, "bit/s"),
        _in("legacy_removed_bps", DataType.NUMBER, "bit/s"),
    ),
    outputs=(_out("data_storage_bits", DataType.NUMBER, "bit"),),
)

COMM_DATA_DEFINITION = ModelDefinition(
    COMM_DATA_REF,
    ModelKind.SUBSYSTEM,
    "Composite communication and data downlink",
    inputs=(
        _in("data_storage_bits", DataType.NUMBER, "bit"),
        _in("pdu_comm_enabled", DataType.BOOLEAN),
        _in("pointing_gate", DataType.BOOLEAN),
        _in("ground_slant_range_m", DataType.NUMBER, "m"),
    ),
    outputs=(
        _out("downlink_delivered_bps", DataType.NUMBER, "bit/s"),
        _out("removed_bps", DataType.NUMBER, "bit/s"),
        _out("legacy_transmitter_removed_bps", DataType.NUMBER, "bit/s"),
        _out("comm_power_w", DataType.NUMBER, "W"),
        _out("comm_heat_w", DataType.NUMBER, "W"),
        _out("rf_link_distance_m", DataType.NUMBER, "m"),
    ),
)

THERMAL_DEFINITION = ModelDefinition(
    THERMAL_REF,
    ModelKind.SUBSYSTEM,
    "Composite thermal network",
    inputs=(
        _in("payload_heat_w", DataType.NUMBER, "W"),
        _in("comm_heat_w", DataType.NUMBER, "W"),
        _in("eps_heat_w", DataType.NUMBER, "W"),
        _in("adcs_heat_w", DataType.NUMBER, "W"),
        _in("propulsion_heat_w", DataType.NUMBER, "W"),
        _in("pdu_heater_enabled", DataType.BOOLEAN),
        _in("eclipse_shadow_factor", DataType.NUMBER, "ratio"),
    ),
    outputs=(
        _out("thermal_temp_c", DataType.NUMBER, "degC"),
        _out("thermal_adcs_temp_c", DataType.NUMBER, "degC"),
        _out("thermal_structure_temp_c", DataType.NUMBER, "degC"),
        _out("thermal_solar_panel_temp_c", DataType.NUMBER, "degC"),
        _out("heater_active_count", DataType.NUMBER, "count"),
        _out("heater_eps_load_w", DataType.NUMBER, "W"),
    ),
)

PROPULSION_DEFINITION = ModelDefinition(
    PROPULSION_REF,
    ModelKind.SUBSYSTEM,
    "Composite propulsion",
    outputs=(
        _out("propellant_remaining_kg", DataType.NUMBER, "kg"),
        _out("propellant_used_kg", DataType.NUMBER, "kg"),
        _out("propulsion_power_w", DataType.NUMBER, "W"),
        _out("propulsion_heat_w", DataType.NUMBER, "W"),
        _out("orbit_perturbation_m", DataType.NUMBER, "m"),
    ),
)

SPACECRAFT_DYNAMICS_DEFINITION = ModelDefinition(
    SPACECRAFT_DYNAMICS_REF,
    ModelKind.COMPONENT,
    "Composite spacecraft dynamics",
    inputs=(
        _in("gravity_accel_m_s2", DataType.NUMBER, "m/s2"),
        _in("propulsion_orbit_delta_m", DataType.NUMBER, "m"),
    ),
    outputs=(
        _out("orbit_radius_m", DataType.NUMBER, "m"),
        _out("spacecraft_position_x_m", DataType.NUMBER, "m"),
        _out("spacecraft_state", DataType.NUMBER),
    ),
)

COMPOSITE_SPACECRAFT_DEFINITION = ModelDefinition(
    COMPOSITE_SPACECRAFT_REF,
    ModelKind.SPACECRAFT,
    "Composite digital twin spacecraft interface",
    description="A4R governed interface for the existing composite whole-spacecraft Basilisk graph.",
    parameters=COMPOSITE_PARAMETERS,
    outputs=(
        _out("battery_soc", DataType.NUMBER, "ratio"),
        _out("thermal_temp_c", DataType.NUMBER, "degC"),
        _out("attitude_error_deg", DataType.NUMBER, "deg"),
        _out("payload_generated_bps", DataType.NUMBER, "bit/s"),
        _out("downlink_delivered_bps", DataType.NUMBER, "bit/s"),
        _out("propellant_remaining_kg", DataType.NUMBER, "kg"),
    ),
    effects=tuple(_effect(item) for item in EFFECT_IDS),
    evidence=COMMON_EVIDENCE,
    tags=("a4r", "composite_digital_twin", "engineering_prototype"),
)


def _ep(node: str, port: str) -> PortEndpoint:
    return PortEndpoint(lid(node), lid(port))


COMPOSITE_GRAPH = ModelGraphDefinition(
    graph_ref=COMPOSITE_SPACECRAFT_REF,
    kind=ModelKind.SPACECRAFT,
    title="Composite digital twin model graph",
    nodes=(
        ModelNode(lid("orbit_environment"), ORBIT_ENVIRONMENT_REF),
        ModelNode(lid("spacecraft_dynamics"), SPACECRAFT_DYNAMICS_REF),
        ModelNode(lid("adcs"), ADCS_REF),
        ModelNode(lid("eps"), EPS_REF),
        ModelNode(lid("payload"), PAYLOAD_REF),
        ModelNode(lid("storage"), DATA_STORAGE_REF),
        ModelNode(lid("comm"), COMM_DATA_REF),
        ModelNode(lid("thermal"), THERMAL_REF),
        ModelNode(lid("propulsion"), PROPULSION_REF),
    ),
    connections=(
        ModelConnection(_ep("payload", "payload_generated_bps"), _ep("storage", "generated_bps"), transform="payload_to_comm_storage"),
        ModelConnection(_ep("payload", "payload_power_w"), _ep("eps", "payload_power_w"), kind=ConnectionKind.RESOURCE, feedback=True, transform="payload_activity_to_eps_thermal"),
        ModelConnection(_ep("orbit_environment", "solar_power_available_w"), _ep("eps", "solar_power_w"), kind=ConnectionKind.RESOURCE, transform="orbit_sun_attitude_eclipse_to_eps_solar_power"),
        ModelConnection(_ep("comm", "removed_bps"), _ep("storage", "removed_bps"), feedback=True, transform="comm_native_odh_to_storage_drain"),
        ModelConnection(_ep("eps", "pdu_payload_enabled"), _ep("payload", "pdu_payload_enabled"), kind=ConnectionKind.RESOURCE, transform="eps_pdu_to_payload_activity"),
        ModelConnection(_ep("adcs", "pointing_gate"), _ep("payload", "pointing_gate"), transform="adcs_pointing_to_payload_comm_gate"),
        ModelConnection(_ep("comm", "comm_power_w"), _ep("eps", "comm_power_w"), kind=ConnectionKind.RESOURCE, feedback=True, transform="comm_activity_to_eps_thermal"),
        ModelConnection(_ep("eps", "pdu_comm_enabled"), _ep("comm", "pdu_comm_enabled"), kind=ConnectionKind.RESOURCE, transform="eps_pdu_to_comm_activity"),
        ModelConnection(_ep("eps", "eps_heat_w"), _ep("thermal", "eps_heat_w"), kind=ConnectionKind.RESOURCE, transform="eps_loads_to_thermal_heat"),
        ModelConnection(_ep("comm", "legacy_transmitter_removed_bps"), _ep("storage", "legacy_removed_bps"), feedback=True, transform="comm_transmitter_to_storage_drain"),
        ModelConnection(_ep("adcs", "adcs_control_power_w"), _ep("eps", "adcs_power_w"), kind=ConnectionKind.RESOURCE, feedback=True, transform="adcs_control_effort_to_eps_thermal"),
        ModelConnection(_ep("spacecraft_dynamics", "spacecraft_position_x_m"), _ep("orbit_environment", "spacecraft_position_x_m"), transform="adcs_spacecraft_to_orbit_environment"),
        ModelConnection(_ep("eps", "pdu_heater_enabled"), _ep("thermal", "pdu_heater_enabled"), kind=ConnectionKind.RESOURCE, transform="eps_pdu_to_thermal_heaters"),
        ModelConnection(_ep("orbit_environment", "gravity_accel_m_s2"), _ep("spacecraft_dynamics", "gravity_accel_m_s2"), kind=ConnectionKind.STATE, feedback=True, transform="gravity_to_spacecraft"),
        ModelConnection(_ep("orbit_environment", "ground_slant_range_m"), _ep("comm", "ground_slant_range_m"), transform="ground_access_rf_link_budget"),
        ModelConnection(_ep("orbit_environment", "eclipse_shadow_factor"), _ep("thermal", "eclipse_shadow_factor"), transform="orbit_eclipse_to_thermal_shadow"),
        ModelConnection(_ep("propulsion", "propulsion_power_w"), _ep("eps", "propulsion_power_w"), kind=ConnectionKind.RESOURCE, feedback=True, transform="propulsion_activity_to_eps_thermal"),
        ModelConnection(_ep("propulsion", "orbit_perturbation_m"), _ep("spacecraft_dynamics", "propulsion_orbit_delta_m"), kind=ConnectionKind.STATE, transform="propulsion_effector_to_spacecraft"),
        ModelConnection(_ep("thermal", "heater_eps_load_w"), _ep("eps", "heater_power_w"), kind=ConnectionKind.RESOURCE, feedback=True, transform="thermal_heaters_to_eps_load"),
        ModelConnection(_ep("storage", "data_storage_bits"), _ep("comm", "data_storage_bits"), feedback=True),
        ModelConnection(_ep("adcs", "pointing_gate"), _ep("comm", "pointing_gate"), feedback=True),
        ModelConnection(_ep("payload", "payload_heat_w"), _ep("thermal", "payload_heat_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("comm", "comm_heat_w"), _ep("thermal", "comm_heat_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("adcs", "thermal_adcs_heat_w"), _ep("thermal", "adcs_heat_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("propulsion", "propulsion_heat_w"), _ep("thermal", "propulsion_heat_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("spacecraft_dynamics", "spacecraft_state"), _ep("adcs", "spacecraft_state"), kind=ConnectionKind.STATE),
    ),
    exposed_outputs=(
        _ep("eps", "battery_soc"),
        _ep("thermal", "thermal_temp_c"),
        _ep("adcs", "attitude_error_deg"),
        _ep("payload", "payload_generated_bps"),
        _ep("comm", "downlink_delivered_bps"),
        _ep("propulsion", "propellant_remaining_kg"),
    ),
)

COMPOSITE_PARAMETER_SET = ParameterSet(
    COMPOSITE_PARAMETER_SET_ID,
    COMPOSITE_SPACECRAFT_REF,
    COMPOSITE_DEFAULT_VALUES,
    source_refs=(
        "whole_spacecraft.schemas.WholeSpacecraftConfig",
        "whole_spacecraft.schemas.WholeSpacecraftRunConfig",
        "src/sat_sim/capabilities/whole_spacecraft.composite_digital_twin.v1.yaml",
        "parameters/data/demo_parameter_registry_v1.json",
    ),
    applicability="A4R composite digital twin migration",
    confidence="engineering_demo",
    notes=("Values preserve v0.6.0.2-A3R-DC1 composite capability defaults; they are not calibration data.",),
)

COMPOSITE_IMPLEMENTATION_PROFILE = ModelImplementationProfile(
    COMPOSITE_IMPLEMENTATION,
    COMPOSITE_SPACECRAFT_REF,
    engine="basilisk",
    fidelity="engineering_prototype_composite",
    adapter_key=COMPOSITE_ADAPTER_KEY,
    supported_inputs=("modifiers", "degradations", "parameters"),
    supported_outputs=(
        "battery_soc",
        "thermal_temp_c",
        "attitude_error_deg",
        "payload_generated_bps",
        "downlink_delivered_bps",
        "propellant_remaining_kg",
    ),
    supported_effects=EFFECT_IDS,
    runtime_requirements=("Basilisk==2.11.0+satfix1",),
    limitations=("Engineering prototype; not flight-data-calibrated or certification-grade.",),
)

COMPOSITE_OBJECT = OperationsObjectDefinition(
    COMPOSITE_OBJECT_REF,
    title="Composite digital twin operations object",
    properties=(
        ObjectPropertySpec(ObjectRef("spacecraft.composite_digital_twin.health_state"), DataType.STRING),
        ObjectPropertySpec(ObjectRef("spacecraft.composite_digital_twin.coupling_status"), DataType.STRING),
    ),
    evidence=(
        ObjectEvidenceSpec(ObjectRef("spacecraft.composite_digital_twin.effect_evidence"), DataType.BOOLEAN),
        ObjectEvidenceSpec(ObjectRef("spacecraft.composite_digital_twin.coupling_evidence"), DataType.BOOLEAN),
    ),
)

COMPOSITE_BINDINGS = BindingSet(
    COMPOSITE_BINDING_SET_ID,
    properties=(
        PropertyBinding(lid("health-state"), ObjectRef("spacecraft.composite_digital_twin.health_state"), ModelPath("summary.status")),
        PropertyBinding(lid("coupling-status"), ObjectRef("spacecraft.composite_digital_twin.coupling_status"), ModelPath("summary.proxy_coupling_runtime_status")),
    ),
    effects=tuple(
        EffectBinding(
            lid(f"effect-{effect_id.replace('_', '-')}"),
            ObjectRef(f"spacecraft.composite_digital_twin.{effect_id}"),
            lid(effect_id),
            lid("whole_spacecraft"),
        )
        for effect_id in EFFECT_IDS
    ),
    evidence=(
        EvidenceBinding(lid("effect-evidence"), lid("effect-active-evidence"), ModelPath("labels.effect_active_count"), ObjectRef("spacecraft.composite_digital_twin.effect_evidence")),
        EvidenceBinding(lid("coupling-evidence"), lid("status-evidence"), ModelPath("summary.proxy_coupling_runtime_status"), ObjectRef("spacecraft.composite_digital_twin.coupling_evidence")),
    ),
)

COMPOSITE_PROJECTION = CapabilityProjection(
    COMPOSITE_PROJECTION_ID,
    COMPOSITE_CAPABILITY_ID,
    COMPOSITE_OBJECT_REF,
    COMPOSITE_SPACECRAFT_REF,
    COMPOSITE_GRAPH.graph_ref,
    COMPOSITE_IMPLEMENTATION,
    COMPOSITE_PARAMETER_SET_ID,
    COMPOSITE_ADAPTER_KEY,
    exposed_parameters=tuple(ExposedParameter(str(item.name), str(item.name)) for item in COMPOSITE_PARAMETERS),
    exposed_properties=tuple(item.ref for item in COMPOSITE_OBJECT.properties),
    exposed_actions=(),
    exposed_effects=tuple(ObjectRef(f"spacecraft.composite_digital_twin.{effect_id}") for effect_id in EFFECT_IDS),
    evidence_refs=tuple(item.ref for item in COMPOSITE_OBJECT.evidence),
    description="Execute the governed whole-spacecraft composite digital-twin model graph.",
)


def build_registry() -> ModelAssetRegistry:
    registry = ModelAssetRegistry()
    for definition in (
        ORBIT_ENVIRONMENT_DEFINITION,
        ADCS_DEFINITION,
        EPS_DEFINITION,
        THERMAL_DEFINITION,
        COMM_DATA_DEFINITION,
        PAYLOAD_DEFINITION,
        PROPULSION_DEFINITION,
        DATA_STORAGE_DEFINITION,
        SPACECRAFT_DYNAMICS_DEFINITION,
        COMPOSITE_SPACECRAFT_DEFINITION,
    ):
        registry.register_definition(definition)
    registry.register_graph(COMPOSITE_GRAPH)
    registry.register_parameter_set(COMPOSITE_PARAMETER_SET)
    registry.validate()
    return registry


COMPOSITE_ASSET_REGISTRY = build_registry()
COMPOSITE_IMPLEMENTATION_PROFILES = {str(COMPOSITE_IMPLEMENTATION): COMPOSITE_IMPLEMENTATION_PROFILE}


def parameter_contracts() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for item in COMPOSITE_SPACECRAFT_DEFINITION.parameters:
        payload: dict[str, object] = {"type": item.data_type.value, "required": item.required}
        if item.unit is not None:
            payload["unit"] = item.unit
        if item.default_is_set:
            payload["default"] = item.default
        if item.minimum is not None:
            payload["min"] = item.minimum
        if item.maximum is not None:
            payload["max"] = item.maximum
        out[str(item.name)] = payload
    return out


def coupling_ids_from_graph() -> set[str]:
    return {str(item.transform) for item in COMPOSITE_GRAPH.connections if item.transform}


__all__ = [name for name in globals() if name.isupper()] + [
    "build_registry",
    "coupling_ids_from_graph",
    "parameter_contracts",
]
