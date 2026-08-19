"""Governed subsystem vertical slices for A5R.

The module migrates four existing, recommended subsystem capabilities into the
model-asset architecture without rewriting their validated physical runners:

* EPS unified Basilisk runtime
* communication/data unified Basilisk runtime
* thermal source-native runtime
* propulsion unified Basilisk runtime

Each vertical defines an M-side model/graph/parameter set, an O-side operations
object, BindingIR, CapabilityProjection and an engine implementation profile.
The concrete runners remain behind non-Legacy execution adapters in
``sat_sim.a5r``.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

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
    StateSpec,
)
from sat_sim_operations import ObjectActionSpec, ObjectEvidenceSpec, ObjectPropertySpec, OperationsObjectDefinition
from sat_sim_projection import CapabilityProjection, ExposedParameter

from .contracts import ModelAssetRegistry, ParameterSet


def lid(value: str) -> LocalId:
    return LocalId(value)


def mref(value: str) -> ModelRef:
    return ModelRef.parse(value)


def _param(
    name: str,
    data_type: DataType,
    default: Any,
    *,
    unit: str | None = None,
    minimum: float | None = None,
    maximum: float | None = None,
    choices: tuple[Any, ...] = (),
    description: str = "",
) -> ParameterSpec:
    return ParameterSpec(
        lid(name),
        data_type,
        unit=unit,
        default_is_set=True,
        default=default,
        minimum=minimum,
        maximum=maximum,
        choices=choices,
        description=description,
    )


def _in(name: str, data_type: DataType, unit: str | None = None) -> PortSpec:
    return PortSpec(lid(name), PortDirection.INPUT, data_type, unit=unit)


def _out(name: str, data_type: DataType, unit: str | None = None) -> PortSpec:
    return PortSpec(lid(name), PortDirection.OUTPUT, data_type, unit=unit)


def _ep(node: str, port: str) -> PortEndpoint:
    return PortEndpoint(lid(node), lid(port))


@dataclass(frozen=True)
class SubsystemAssetBundle:
    capability_id: str
    adapter_key: str
    root_definition: ModelDefinition
    graph: ModelGraphDefinition
    parameter_set: ParameterSet
    implementation_profile: ModelImplementationProfile
    operations_object: OperationsObjectDefinition
    binding_set: BindingSet
    projection: CapabilityProjection
    required_outputs: tuple[str, ...]
    diagnostics_key: str

    @property
    def model_ref(self) -> ModelRef:
        return self.root_definition.model_ref

    @property
    def implementation_id(self) -> ImplementationId:
        return self.implementation_profile.implementation_id


# ---------------------------------------------------------------------------
# EPS vertical
# ---------------------------------------------------------------------------
EPS_CAPABILITY_ID = "subsystem.eps.unified_native.v1"
EPS_ADAPTER_KEY = "basilisk.eps_unified_graph"
EPS_ROOT_REF = mref("subsystem.eps_unified_native@1")
EPS_IMPLEMENTATION = ImplementationId.parse("subsystem.eps_unified_native.basilisk@1")
EPS_PARAMETER_SET_ID = lid("eps-unified-native-demo-v1")
EPS_BINDING_SET_ID = lid("eps-unified-native-bindings-v1")
EPS_PROJECTION_ID = lid("eps-unified-native-projection-v1")
EPS_OBJECT_REF = ObjectRef("spacecraft.eps")

EPS_PARAMETERS = (
    _param("battery_capacity_wh", DataType.NUMBER, 160.0, unit="Wh", minimum=0.1),
    _param("initial_soc", DataType.NUMBER, 0.62, unit="1", minimum=0.0, maximum=1.0),
    _param("solar_power_w", DataType.NUMBER, 95.0, unit="W", minimum=0.0),
    _param("solar_efficiency", DataType.NUMBER, 0.25, unit="1", minimum=0.0, maximum=1.0),
    _param("bus_power_w", DataType.NUMBER, 18.0, unit="W", minimum=0.0),
    _param("payload_power_w", DataType.NUMBER, 38.0, unit="W", minimum=0.0),
    _param("adcs_power_w", DataType.NUMBER, 20.0, unit="W", minimum=0.0),
    _param("comm_power_w", DataType.NUMBER, 12.0, unit="W", minimum=0.0),
    _param("payload_min_soc", DataType.NUMBER, 0.55, unit="1", minimum=0.0, maximum=1.0),
    _param("comm_min_soc", DataType.NUMBER, 0.50, unit="1", minimum=0.0, maximum=1.0),
    # Existing implementation defaults are governed but intentionally not exposed.
    _param("use_simple_solar_panel", DataType.BOOLEAN, True),
    _param("heater_power_w", DataType.NUMBER, 0.0, unit="W", minimum=0.0),
    _param("heater_min_soc", DataType.NUMBER, 0.30, unit="1", minimum=0.0, maximum=1.0),
    _param("adcs_min_soc", DataType.NUMBER, 0.20, unit="1", minimum=0.0, maximum=1.0),
    _param("recovery_soc", DataType.NUMBER, 0.65, unit="1", minimum=0.0, maximum=1.0),
)
EPS_PUBLIC_PARAMETERS = (
    "battery_capacity_wh", "initial_soc", "solar_power_w", "solar_efficiency",
    "bus_power_w", "payload_power_w", "adcs_power_w", "comm_power_w",
    "payload_min_soc", "comm_min_soc",
)

EPS_POWER_SOURCE_REF = mref("component.eps_native_power_source@1")
EPS_PDU_REF = mref("component.eps_native_pdu@1")
EPS_BUS_REF = mref("component.eps_native_power_bus@1")
EPS_BATTERY_REF = mref("component.eps_native_battery@1")

EPS_POWER_SOURCE_DEFINITION = ModelDefinition(
    EPS_POWER_SOURCE_REF, ModelKind.COMPONENT, "EPS native solar power source",
    outputs=(_out("solar_power_w", DataType.NUMBER, "W"),),
)
EPS_PDU_DEFINITION = ModelDefinition(
    EPS_PDU_REF, ModelKind.COMPONENT, "EPS native power distribution controller",
    inputs=(_in("battery_soc", DataType.NUMBER, "1"),),
    outputs=(
        _out("total_load_w", DataType.NUMBER, "W"),
        _out("payload_load_enabled_w", DataType.NUMBER, "W"),
        _out("adcs_load_enabled_w", DataType.NUMBER, "W"),
        _out("comm_load_enabled_w", DataType.NUMBER, "W"),
        _out("heater_load_enabled_w", DataType.NUMBER, "W"),
        _out("load_shed_active", DataType.BOOLEAN),
    ),
)
EPS_BUS_DEFINITION = ModelDefinition(
    EPS_BUS_REF, ModelKind.COMPONENT, "EPS native power balance",
    inputs=(_in("solar_power_w", DataType.NUMBER, "W"), _in("load_power_w", DataType.NUMBER, "W")),
    outputs=(_out("net_power_w", DataType.NUMBER, "W"),),
)
EPS_BATTERY_DEFINITION = ModelDefinition(
    EPS_BATTERY_REF, ModelKind.COMPONENT, "EPS native battery storage",
    inputs=(_in("net_power_w", DataType.NUMBER, "W"),),
    outputs=(
        _out("battery_storage_j", DataType.NUMBER, "J"),
        _out("battery_capacity_j", DataType.NUMBER, "J"),
        _out("battery_soc", DataType.NUMBER, "1"),
    ),
    states=(StateSpec(lid("stored_energy_j"), DataType.NUMBER, unit="J"),),
)
EPS_ROOT_DEFINITION = ModelDefinition(
    EPS_ROOT_REF, ModelKind.SUBSYSTEM, "Governed EPS unified native subsystem",
    parameters=EPS_PARAMETERS,
    outputs=(
        _out("battery_storage_j", DataType.NUMBER, "J"),
        _out("battery_capacity_j", DataType.NUMBER, "J"),
        _out("battery_soc", DataType.NUMBER, "1"),
        _out("solar_power_w", DataType.NUMBER, "W"),
        _out("net_power_w", DataType.NUMBER, "W"),
        _out("load_shed_active", DataType.BOOLEAN),
    ),
    effects=(EffectSpec(lid("nominal"), EffectKind.MODE, evidence_ids=(lid("soc-evidence"),)),),
    evidence=(
        EvidenceSpec(lid("soc-evidence"), ModelPath("eps.battery_soc"), DataType.NUMBER, unit="1"),
        EvidenceSpec(lid("load-shed-evidence"), ModelPath("eps.load_shed_active"), DataType.BOOLEAN),
    ),
    tags=("a5r", "eps", "basilisk_native", "engineering_preview"),
)
EPS_GRAPH = ModelGraphDefinition(
    EPS_ROOT_REF, ModelKind.SUBSYSTEM, "EPS unified native model graph",
    nodes=(
        ModelNode(lid("power_source"), EPS_POWER_SOURCE_REF),
        ModelNode(lid("pdu"), EPS_PDU_REF),
        ModelNode(lid("power_bus"), EPS_BUS_REF),
        ModelNode(lid("battery"), EPS_BATTERY_REF),
    ),
    connections=(
        ModelConnection(_ep("power_source", "solar_power_w"), _ep("power_bus", "solar_power_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("pdu", "total_load_w"), _ep("power_bus", "load_power_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("power_bus", "net_power_w"), _ep("battery", "net_power_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("battery", "battery_soc"), _ep("pdu", "battery_soc"), kind=ConnectionKind.STATE, feedback=True),
    ),
    exposed_outputs=(
        _ep("battery", "battery_storage_j"), _ep("battery", "battery_capacity_j"),
        _ep("battery", "battery_soc"), _ep("power_source", "solar_power_w"),
        _ep("power_bus", "net_power_w"), _ep("pdu", "load_shed_active"),
    ),
)
EPS_PARAMETER_SET = ParameterSet(
    EPS_PARAMETER_SET_ID, EPS_ROOT_REF, {str(p.name): p.default for p in EPS_PARAMETERS},
    source_refs=(
        "src/sat_sim/capabilities/subsystem.eps.unified_native.v1.yaml",
        "sat_sim.adapters.subsystem_eps_unified_native.EpsUnifiedNativeAdapter",
        "subsystems.eps.schemas.EPSBasiliskConfig",
    ),
    applicability="A5R focused EPS subsystem migration",
    confidence="engineering_demo",
)
EPS_PROFILE = ModelImplementationProfile(
    EPS_IMPLEMENTATION, EPS_ROOT_REF, engine="basilisk", fidelity="unified_native",
    adapter_key=EPS_ADAPTER_KEY,
    supported_outputs=("battery_soc", "solar_power_w", "net_power_w", "load_shed_active"),
    supported_effects=("nominal",),
    limitations=("Independent nominal EPS capability; runtime faults and degradations remain whole-spacecraft only.",),
    runtime_requirements=("Basilisk==2.11.0+satfix1",),
)
EPS_OBJECT = OperationsObjectDefinition(
    EPS_OBJECT_REF, "EPS operations object",
    properties=(
        ObjectPropertySpec(ObjectRef("spacecraft.eps.soc"), DataType.NUMBER, unit="1"),
        ObjectPropertySpec(ObjectRef("spacecraft.eps.power_balance_w"), DataType.NUMBER, unit="W"),
        ObjectPropertySpec(ObjectRef("spacecraft.eps.load_shed_state"), DataType.BOOLEAN),
    ),
    effects=(ObjectActionSpec(ObjectRef("spacecraft.eps.nominal")),),
    evidence=(ObjectEvidenceSpec(ObjectRef("spacecraft.eps.soc_evidence"), DataType.NUMBER),),
)
EPS_BINDINGS = BindingSet(
    EPS_BINDING_SET_ID,
    properties=(
        PropertyBinding(lid("soc"), ObjectRef("spacecraft.eps.soc"), ModelPath("eps.battery_soc")),
        PropertyBinding(lid("power-balance"), ObjectRef("spacecraft.eps.power_balance_w"), ModelPath("eps.net_power_w")),
        PropertyBinding(lid("load-shed"), ObjectRef("spacecraft.eps.load_shed_state"), ModelPath("eps.load_shed_active")),
    ),
    effects=(EffectBinding(lid("nominal-effect"), ObjectRef("spacecraft.eps.nominal"), lid("nominal"), lid("eps")),),
    evidence=(EvidenceBinding(lid("soc-evidence"), lid("soc-evidence"), ModelPath("eps.battery_soc"), ObjectRef("spacecraft.eps.soc_evidence")),),
)
EPS_PROJECTION = CapabilityProjection(
    EPS_PROJECTION_ID, EPS_CAPABILITY_ID, EPS_OBJECT_REF, EPS_ROOT_REF, EPS_GRAPH.graph_ref,
    EPS_IMPLEMENTATION, EPS_PARAMETER_SET_ID, EPS_ADAPTER_KEY,
    exposed_parameters=tuple(ExposedParameter(name, name) for name in EPS_PUBLIC_PARAMETERS),
    exposed_properties=tuple(item.ref for item in EPS_OBJECT.properties),
    exposed_actions=(),
    exposed_effects=tuple(item.ref for item in EPS_OBJECT.effects),
    evidence_refs=tuple(item.ref for item in EPS_OBJECT.evidence),
    description="Execute the governed EPS unified-native subsystem graph.",
)
EPS_REQUIRED_OUTPUTS = (
    "eps.battery_storage_j", "eps.battery_capacity_j", "eps.battery_soc",
    "eps.solar_power_w", "eps.bus_load_w", "eps.payload_load_enabled_w",
    "eps.adcs_load_enabled_w", "eps.comm_load_enabled_w", "eps.heater_load_enabled_w",
    "eps.net_power_w", "eps.load_shed_active",
)


# ---------------------------------------------------------------------------
# Communication/data vertical
# ---------------------------------------------------------------------------
COMM_CAPABILITY_ID = "subsystem.comm_data.unified_native.v1"
COMM_ADAPTER_KEY = "basilisk.comm_data_unified_graph"
COMM_ROOT_REF = mref("subsystem.comm_data_unified_native@1")
COMM_IMPLEMENTATION = ImplementationId.parse("subsystem.comm_data_unified_native.basilisk@1")
COMM_PARAMETER_SET_ID = lid("comm-data-unified-native-demo-v1")
COMM_BINDING_SET_ID = lid("comm-data-unified-native-bindings-v1")
COMM_PROJECTION_ID = lid("comm-data-unified-native-projection-v1")
COMM_OBJECT_REF = ObjectRef("spacecraft.comm_data")

COMM_PARAMETERS = (
    _param("instrument_baud_bps", DataType.NUMBER, 2_500_000.0, unit="bit/s", minimum=0.0),
    _param("storage_capacity_bits", DataType.NUMBER, 6_000_000_000.0, unit="bit", minimum=1.0),
    _param("transmitter_baud_bps", DataType.NUMBER, 1_500_000.0, unit="bit/s", minimum=0.0),
    _param("initial_storage_bits", DataType.NUMBER, 0.0, unit="bit", minimum=0.0),
    _param("native_storage_drain_enabled", DataType.BOOLEAN, True),
)
COMM_PUBLIC_PARAMETERS = tuple(str(item.name) for item in COMM_PARAMETERS)
COMM_INSTRUMENT_REF = mref("component.comm_native_instrument@1")
COMM_STORAGE_REF = mref("component.comm_native_storage@1")
COMM_TRANSMITTER_REF = mref("component.comm_native_transmitter@1")

COMM_INSTRUMENT_DEFINITION = ModelDefinition(
    COMM_INSTRUMENT_REF, ModelKind.COMPONENT, "Native data instrument",
    outputs=(_out("instrument_baud_bps", DataType.NUMBER, "bit/s"),),
)
COMM_STORAGE_DEFINITION = ModelDefinition(
    COMM_STORAGE_REF, ModelKind.COMPONENT, "Native onboard storage",
    inputs=(_in("generated_bps", DataType.NUMBER, "bit/s"), _in("removed_bps", DataType.NUMBER, "bit/s")),
    outputs=(_out("storage_level_bits", DataType.NUMBER, "bit"), _out("storage_capacity_bits", DataType.NUMBER, "bit")),
    states=(StateSpec(lid("stored_bits"), DataType.NUMBER, unit="bit"),),
)
COMM_TRANSMITTER_DEFINITION = ModelDefinition(
    COMM_TRANSMITTER_REF, ModelKind.COMPONENT, "Native data transmitter",
    inputs=(_in("storage_level_bits", DataType.NUMBER, "bit"),),
    outputs=(
        _out("removed_bps", DataType.NUMBER, "bit/s"),
        _out("transmitter_baud_bps", DataType.NUMBER, "bit/s"),
        _out("native_storage_drain_enabled", DataType.BOOLEAN),
        _out("transmitter_storage_node_baud_bps", DataType.NUMBER, "bit/s"),
    ),
)
COMM_ROOT_DEFINITION = ModelDefinition(
    COMM_ROOT_REF, ModelKind.SUBSYSTEM, "Governed communication/data unified subsystem",
    parameters=COMM_PARAMETERS,
    outputs=(
        _out("instrument_baud_bps", DataType.NUMBER, "bit/s"),
        _out("storage_level_bits", DataType.NUMBER, "bit"),
        _out("storage_capacity_bits", DataType.NUMBER, "bit"),
        _out("transmitter_baud_bps", DataType.NUMBER, "bit/s"),
        _out("native_storage_drain_enabled", DataType.BOOLEAN),
        _out("transmitter_storage_node_baud_bps", DataType.NUMBER, "bit/s"),
    ),
    effects=(EffectSpec(lid("nominal"), EffectKind.MODE, evidence_ids=(lid("storage-evidence"),)),),
    evidence=(EvidenceSpec(lid("storage-evidence"), ModelPath("comm_data.storage_level_bits"), DataType.NUMBER, unit="bit"),),
    tags=("a5r", "comm_data", "basilisk_native", "engineering_preview"),
)
COMM_GRAPH = ModelGraphDefinition(
    COMM_ROOT_REF, ModelKind.SUBSYSTEM, "Communication/data unified native graph",
    nodes=(
        ModelNode(lid("instrument"), COMM_INSTRUMENT_REF),
        ModelNode(lid("storage"), COMM_STORAGE_REF),
        ModelNode(lid("transmitter"), COMM_TRANSMITTER_REF),
    ),
    connections=(
        ModelConnection(_ep("instrument", "instrument_baud_bps"), _ep("storage", "generated_bps")),
        ModelConnection(_ep("storage", "storage_level_bits"), _ep("transmitter", "storage_level_bits"), kind=ConnectionKind.STATE),
        ModelConnection(_ep("transmitter", "removed_bps"), _ep("storage", "removed_bps"), feedback=True),
    ),
    exposed_outputs=(
        _ep("instrument", "instrument_baud_bps"), _ep("storage", "storage_level_bits"),
        _ep("storage", "storage_capacity_bits"), _ep("transmitter", "transmitter_baud_bps"),
        _ep("transmitter", "native_storage_drain_enabled"),
        _ep("transmitter", "transmitter_storage_node_baud_bps"),
    ),
)
COMM_PARAMETER_SET = ParameterSet(
    COMM_PARAMETER_SET_ID, COMM_ROOT_REF, {str(p.name): p.default for p in COMM_PARAMETERS},
    source_refs=(
        "src/sat_sim/capabilities/subsystem.comm_data.unified_native.v1.yaml",
        "sat_sim.adapters.subsystem_comm_data_unified_native.CommDataUnifiedNativeAdapter",
    ),
    applicability="A5R focused communication/data migration", confidence="engineering_demo",
)
COMM_PROFILE = ModelImplementationProfile(
    COMM_IMPLEMENTATION, COMM_ROOT_REF, engine="basilisk", fidelity="official_native",
    adapter_key=COMM_ADAPTER_KEY,
    supported_outputs=("instrument_baud_bps", "storage_level_bits", "transmitter_baud_bps"),
    supported_effects=("nominal",),
    limitations=("Independent nominal data path; no ground-access geometry or fault effects.",),
    runtime_requirements=("Basilisk==2.11.0+satfix1",),
)
COMM_OBJECT = OperationsObjectDefinition(
    COMM_OBJECT_REF, "Communication/data operations object",
    properties=(
        ObjectPropertySpec(ObjectRef("spacecraft.comm_data.storage_bits"), DataType.NUMBER, unit="bit"),
        ObjectPropertySpec(ObjectRef("spacecraft.comm_data.downlink_baud_bps"), DataType.NUMBER, unit="bit/s"),
        ObjectPropertySpec(ObjectRef("spacecraft.comm_data.storage_drain_enabled"), DataType.BOOLEAN),
    ),
    effects=(ObjectActionSpec(ObjectRef("spacecraft.comm_data.nominal")),),
    evidence=(ObjectEvidenceSpec(ObjectRef("spacecraft.comm_data.storage_evidence"), DataType.NUMBER),),
)
COMM_BINDINGS = BindingSet(
    COMM_BINDING_SET_ID,
    properties=(
        PropertyBinding(lid("storage-bits"), ObjectRef("spacecraft.comm_data.storage_bits"), ModelPath("comm_data.storage_level_bits")),
        PropertyBinding(lid("downlink-baud"), ObjectRef("spacecraft.comm_data.downlink_baud_bps"), ModelPath("comm_data.transmitter_baud_bps")),
        PropertyBinding(lid("storage-drain"), ObjectRef("spacecraft.comm_data.storage_drain_enabled"), ModelPath("comm_data.native_storage_drain_enabled")),
    ),
    effects=(EffectBinding(lid("nominal-effect"), ObjectRef("spacecraft.comm_data.nominal"), lid("nominal"), lid("comm_data")),),
    evidence=(EvidenceBinding(lid("storage-evidence"), lid("storage-evidence"), ModelPath("comm_data.storage_level_bits"), ObjectRef("spacecraft.comm_data.storage_evidence")),),
)
COMM_PROJECTION = CapabilityProjection(
    COMM_PROJECTION_ID, COMM_CAPABILITY_ID, COMM_OBJECT_REF, COMM_ROOT_REF, COMM_GRAPH.graph_ref,
    COMM_IMPLEMENTATION, COMM_PARAMETER_SET_ID, COMM_ADAPTER_KEY,
    exposed_parameters=tuple(ExposedParameter(name, name) for name in COMM_PUBLIC_PARAMETERS),
    exposed_properties=tuple(item.ref for item in COMM_OBJECT.properties),
    exposed_actions=(), exposed_effects=tuple(item.ref for item in COMM_OBJECT.effects),
    evidence_refs=tuple(item.ref for item in COMM_OBJECT.evidence),
    description="Execute the governed communication/data unified-native subsystem graph.",
)
COMM_REQUIRED_OUTPUTS = (
    "comm_data.instrument_baud_bps", "comm_data.storage_level_bits",
    "comm_data.storage_capacity_bits", "comm_data.transmitter_baud_bps",
    "comm_data.native_storage_drain_enabled", "comm_data.transmitter_storage_node_baud_bps",
)


# ---------------------------------------------------------------------------
# Thermal vertical
# ---------------------------------------------------------------------------
THERMAL_CAPABILITY_ID = "subsystem.thermal.source_native.v1"
THERMAL_ADAPTER_KEY = "python.thermal_source_native_graph"
THERMAL_ROOT_REF = mref("subsystem.thermal_source_native@1")
THERMAL_IMPLEMENTATION = ImplementationId.parse("subsystem.thermal_source_native.python@1")
THERMAL_PARAMETER_SET_ID = lid("thermal-source-native-demo-v1")
THERMAL_BINDING_SET_ID = lid("thermal-source-native-bindings-v1")
THERMAL_PROJECTION_ID = lid("thermal-source-native-projection-v1")
THERMAL_OBJECT_REF = ObjectRef("spacecraft.thermal")

THERMAL_PARAMETERS = (
    _param("initial_battery_temp_k", DataType.NUMBER, 290.0, unit="K"),
    _param("payload_power_w", DataType.NUMBER, 20.0, unit="W", minimum=0.0),
    _param("eps_power_w", DataType.NUMBER, 5.0, unit="W", minimum=0.0),
    _param("shadow_factor", DataType.NUMBER, 1.0, unit="1", minimum=0.0, maximum=1.0),
    _param("heater_enabled", DataType.BOOLEAN, True),
)
THERMAL_PUBLIC_PARAMETERS = tuple(str(item.name) for item in THERMAL_PARAMETERS)
THERMAL_ENV_REF = mref("component.thermal_source_environment@1")
THERMAL_LOAD_REF = mref("component.thermal_source_heat_loads@1")
THERMAL_NETWORK_REF = mref("component.thermal_source_network@1")
THERMAL_HEATER_REF = mref("component.thermal_source_heater_controller@1")

THERMAL_ENV_DEFINITION = ModelDefinition(
    THERMAL_ENV_REF, ModelKind.COMPONENT, "Thermal environment input",
    outputs=(_out("shadow_factor", DataType.NUMBER, "1"),),
)
THERMAL_LOAD_DEFINITION = ModelDefinition(
    THERMAL_LOAD_REF, ModelKind.COMPONENT, "Subsystem heat loads",
    outputs=(_out("payload_heat_w", DataType.NUMBER, "W"), _out("eps_heat_w", DataType.NUMBER, "W")),
)
THERMAL_NETWORK_DEFINITION = ModelDefinition(
    THERMAL_NETWORK_REF, ModelKind.COMPONENT, "Reduced lumped thermal network",
    inputs=(
        _in("payload_heat_w", DataType.NUMBER, "W"), _in("eps_heat_w", DataType.NUMBER, "W"),
        _in("shadow_factor", DataType.NUMBER, "1"), _in("heater_power_w", DataType.NUMBER, "W"),
    ),
    outputs=(
        _out("battery_temp_k", DataType.NUMBER, "K"),
        _out("electronics_temp_k", DataType.NUMBER, "K"),
        _out("payload_temp_k", DataType.NUMBER, "K"),
        _out("thermal_safe_request", DataType.BOOLEAN),
    ),
)
THERMAL_HEATER_DEFINITION = ModelDefinition(
    THERMAL_HEATER_REF, ModelKind.COMPONENT, "Thermal heater controller",
    inputs=(_in("controlled_temp_k", DataType.NUMBER, "K"),),
    outputs=(_out("heater_power_w", DataType.NUMBER, "W"),),
)
THERMAL_ROOT_DEFINITION = ModelDefinition(
    THERMAL_ROOT_REF, ModelKind.SUBSYSTEM, "Governed thermal source-native subsystem",
    parameters=THERMAL_PARAMETERS,
    outputs=(
        _out("battery_temp_k", DataType.NUMBER, "K"),
        _out("electronics_temp_k", DataType.NUMBER, "K"),
        _out("payload_temp_k", DataType.NUMBER, "K"),
        _out("heater_power_w", DataType.NUMBER, "W"),
        _out("thermal_safe_request", DataType.BOOLEAN),
    ),
    effects=(EffectSpec(lid("nominal"), EffectKind.MODE, evidence_ids=(lid("thermal-safety-evidence"),)),),
    evidence=(EvidenceSpec(lid("thermal-safety-evidence"), ModelPath("thermal.source_native.thermal_safe_request"), DataType.BOOLEAN),),
    tags=("a5r", "thermal", "source_native", "reduced_order"),
)
THERMAL_GRAPH = ModelGraphDefinition(
    THERMAL_ROOT_REF, ModelKind.SUBSYSTEM, "Thermal source-native model graph",
    nodes=(
        ModelNode(lid("environment"), THERMAL_ENV_REF),
        ModelNode(lid("heat_loads"), THERMAL_LOAD_REF),
        ModelNode(lid("network"), THERMAL_NETWORK_REF),
        ModelNode(lid("heater"), THERMAL_HEATER_REF),
    ),
    connections=(
        ModelConnection(_ep("environment", "shadow_factor"), _ep("network", "shadow_factor")),
        ModelConnection(_ep("heat_loads", "payload_heat_w"), _ep("network", "payload_heat_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("heat_loads", "eps_heat_w"), _ep("network", "eps_heat_w"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("network", "battery_temp_k"), _ep("heater", "controlled_temp_k"), kind=ConnectionKind.STATE),
        ModelConnection(_ep("heater", "heater_power_w"), _ep("network", "heater_power_w"), kind=ConnectionKind.RESOURCE, feedback=True),
    ),
    exposed_outputs=(
        _ep("network", "battery_temp_k"), _ep("network", "electronics_temp_k"),
        _ep("network", "payload_temp_k"), _ep("heater", "heater_power_w"),
        _ep("network", "thermal_safe_request"),
    ),
)
THERMAL_PARAMETER_SET = ParameterSet(
    THERMAL_PARAMETER_SET_ID, THERMAL_ROOT_REF, {str(p.name): p.default for p in THERMAL_PARAMETERS},
    source_refs=(
        "src/sat_sim/capabilities/subsystem.thermal.source_native.v1.yaml",
        "sat_sim.adapters.subsystem_source_native.ThermalSourceNativeAdapter",
        "subsystems.thermal.builder",
    ),
    applicability="A5R thermal source-native migration", confidence="engineering_demo",
)
THERMAL_PROFILE = ModelImplementationProfile(
    THERMAL_IMPLEMENTATION, THERMAL_ROOT_REF, engine="python", fidelity="reduced_lumped",
    adapter_key=THERMAL_ADAPTER_KEY,
    supported_outputs=("battery_temp_k", "electronics_temp_k", "payload_temp_k", "heater_power_w", "thermal_safe_request"),
    supported_effects=("nominal",),
    limitations=("Reduced lumped/source-native model; not finite-element thermal analysis.",),
)
THERMAL_OBJECT = OperationsObjectDefinition(
    THERMAL_OBJECT_REF, "Thermal operations object",
    properties=(
        ObjectPropertySpec(ObjectRef("spacecraft.thermal.battery_temp_k"), DataType.NUMBER, unit="K"),
        ObjectPropertySpec(ObjectRef("spacecraft.thermal.electronics_temp_k"), DataType.NUMBER, unit="K"),
        ObjectPropertySpec(ObjectRef("spacecraft.thermal.safe_request"), DataType.BOOLEAN),
    ),
    effects=(ObjectActionSpec(ObjectRef("spacecraft.thermal.nominal")),),
    evidence=(ObjectEvidenceSpec(ObjectRef("spacecraft.thermal.safety_evidence"), DataType.BOOLEAN),),
)
THERMAL_BINDINGS = BindingSet(
    THERMAL_BINDING_SET_ID,
    properties=(
        PropertyBinding(lid("battery-temp"), ObjectRef("spacecraft.thermal.battery_temp_k"), ModelPath("thermal.source_native.battery_temp_k")),
        PropertyBinding(lid("electronics-temp"), ObjectRef("spacecraft.thermal.electronics_temp_k"), ModelPath("thermal.source_native.electronics_temp_k")),
        PropertyBinding(lid("safe-request"), ObjectRef("spacecraft.thermal.safe_request"), ModelPath("thermal.source_native.thermal_safe_request")),
    ),
    effects=(EffectBinding(lid("nominal-effect"), ObjectRef("spacecraft.thermal.nominal"), lid("nominal"), lid("thermal")),),
    evidence=(EvidenceBinding(lid("safety-evidence"), lid("thermal-safety-evidence"), ModelPath("thermal.source_native.thermal_safe_request"), ObjectRef("spacecraft.thermal.safety_evidence")),),
)
THERMAL_PROJECTION = CapabilityProjection(
    THERMAL_PROJECTION_ID, THERMAL_CAPABILITY_ID, THERMAL_OBJECT_REF, THERMAL_ROOT_REF,
    THERMAL_GRAPH.graph_ref, THERMAL_IMPLEMENTATION, THERMAL_PARAMETER_SET_ID, THERMAL_ADAPTER_KEY,
    exposed_parameters=tuple(ExposedParameter(name, name) for name in THERMAL_PUBLIC_PARAMETERS),
    exposed_properties=tuple(item.ref for item in THERMAL_OBJECT.properties),
    exposed_actions=(), exposed_effects=(),
    evidence_refs=tuple(item.ref for item in THERMAL_OBJECT.evidence),
    description="Execute the governed reduced-order thermal source-native subsystem graph.",
)
THERMAL_REQUIRED_OUTPUTS = (
    "thermal.source_native.battery_temp_k", "thermal.source_native.electronics_temp_k",
    "thermal.source_native.payload_temp_k", "thermal.source_native.heater_power_w",
    "thermal.source_native.thermal_safe_request",
)


# ---------------------------------------------------------------------------
# Propulsion vertical
# ---------------------------------------------------------------------------
PROPULSION_CAPABILITY_ID = "subsystem.propulsion.unified_native.v1"
PROPULSION_ADAPTER_KEY = "basilisk.propulsion_unified_graph"
PROPULSION_ROOT_REF = mref("subsystem.propulsion_unified_native@1")
PROPULSION_IMPLEMENTATION = ImplementationId.parse("subsystem.propulsion_unified_native.basilisk@1")
PROPULSION_PARAMETER_SET_ID = lid("propulsion-unified-native-demo-v1")
PROPULSION_BINDING_SET_ID = lid("propulsion-unified-native-bindings-v1")
PROPULSION_PROJECTION_ID = lid("propulsion-unified-native-projection-v1")
PROPULSION_OBJECT_REF = ObjectRef("spacecraft.propulsion")

PROPULSION_PARAMETERS = (
    _param("spacecraft_mass_kg", DataType.NUMBER, 10.0, unit="kg", minimum=0.1),
    _param("initial_propellant_kg", DataType.NUMBER, 1.0, unit="kg", minimum=0.0),
    _param("tank_capacity_kg", DataType.NUMBER, 2.0, unit="kg", minimum=0.001),
    _param("burn_start_s", DataType.NUMBER, 0.0, unit="s", minimum=0.0),
    _param("burn_on_time_s", DataType.NUMBER, 0.5, unit="s", minimum=0.0),
)
PROPULSION_PUBLIC_PARAMETERS = tuple(str(item.name) for item in PROPULSION_PARAMETERS)
PROPULSION_SCHEDULER_REF = mref("component.propulsion_native_burn_scheduler@1")
PROPULSION_THRUSTER_REF = mref("component.propulsion_native_thruster_array@1")
PROPULSION_TANK_REF = mref("component.propulsion_native_fuel_tank@1")
PROPULSION_DYNAMICS_REF = mref("component.propulsion_native_spacecraft_dynamics@1")

PROPULSION_SCHEDULER_DEFINITION = ModelDefinition(
    PROPULSION_SCHEDULER_REF, ModelKind.COMPONENT, "Burn command scheduler",
    outputs=(_out("burn_command_s", DataType.NUMBER, "s"),),
)
PROPULSION_THRUSTER_DEFINITION = ModelDefinition(
    PROPULSION_THRUSTER_REF, ModelKind.COMPONENT, "Native thruster dynamic effector",
    inputs=(_in("burn_command_s", DataType.NUMBER, "s"), _in("fuel_mass_kg", DataType.NUMBER, "kg")),
    outputs=(
        _out("thrust_force_n", DataType.NUMBER, "N"),
        _out("thrust_force_b_x_n", DataType.NUMBER, "N"),
        _out("thrust_factor", DataType.NUMBER, "1"),
        _out("fuel_mass_dot_kg_s", DataType.NUMBER, "kg/s"),
    ),
)
PROPULSION_TANK_DEFINITION = ModelDefinition(
    PROPULSION_TANK_REF, ModelKind.COMPONENT, "Native fuel tank",
    inputs=(_in("fuel_mass_dot_kg_s", DataType.NUMBER, "kg/s"),),
    outputs=(_out("fuel_mass_kg", DataType.NUMBER, "kg"),),
    states=(StateSpec(lid("fuel_mass_kg"), DataType.NUMBER, unit="kg"),),
)
PROPULSION_DYNAMICS_DEFINITION = ModelDefinition(
    PROPULSION_DYNAMICS_REF, ModelKind.COMPONENT, "Propulsion-coupled spacecraft dynamics",
    inputs=(_in("thrust_force_n", DataType.NUMBER, "N"),),
    outputs=(_out("position_x_m", DataType.NUMBER, "m"), _out("velocity_x_m_s", DataType.NUMBER, "m/s")),
)
PROPULSION_ROOT_DEFINITION = ModelDefinition(
    PROPULSION_ROOT_REF, ModelKind.SUBSYSTEM, "Governed propulsion unified-native subsystem",
    parameters=PROPULSION_PARAMETERS,
    outputs=(
        _out("position_x_m", DataType.NUMBER, "m"), _out("velocity_x_m_s", DataType.NUMBER, "m/s"),
        _out("fuel_mass_kg", DataType.NUMBER, "kg"), _out("fuel_mass_dot_kg_s", DataType.NUMBER, "kg/s"),
        _out("thrust_force_n", DataType.NUMBER, "N"), _out("thrust_force_b_x_n", DataType.NUMBER, "N"),
        _out("thrust_factor", DataType.NUMBER, "1"),
    ),
    effects=(EffectSpec(lid("nominal"), EffectKind.MODE, evidence_ids=(lid("propellant-evidence"),)),),
    evidence=(EvidenceSpec(lid("propellant-evidence"), ModelPath("propulsion.fuel_mass_kg"), DataType.NUMBER, unit="kg"),),
    tags=("a5r", "propulsion", "basilisk_native", "engineering_preview"),
)
PROPULSION_GRAPH = ModelGraphDefinition(
    PROPULSION_ROOT_REF, ModelKind.SUBSYSTEM, "Propulsion unified native model graph",
    nodes=(
        ModelNode(lid("scheduler"), PROPULSION_SCHEDULER_REF),
        ModelNode(lid("thruster"), PROPULSION_THRUSTER_REF),
        ModelNode(lid("fuel_tank"), PROPULSION_TANK_REF),
        ModelNode(lid("spacecraft_dynamics"), PROPULSION_DYNAMICS_REF),
    ),
    connections=(
        ModelConnection(_ep("scheduler", "burn_command_s"), _ep("thruster", "burn_command_s")),
        ModelConnection(_ep("fuel_tank", "fuel_mass_kg"), _ep("thruster", "fuel_mass_kg"), kind=ConnectionKind.STATE, feedback=True),
        ModelConnection(_ep("thruster", "fuel_mass_dot_kg_s"), _ep("fuel_tank", "fuel_mass_dot_kg_s"), kind=ConnectionKind.RESOURCE),
        ModelConnection(_ep("thruster", "thrust_force_n"), _ep("spacecraft_dynamics", "thrust_force_n"), kind=ConnectionKind.STATE),
    ),
    exposed_outputs=(
        _ep("spacecraft_dynamics", "position_x_m"), _ep("spacecraft_dynamics", "velocity_x_m_s"),
        _ep("fuel_tank", "fuel_mass_kg"), _ep("thruster", "fuel_mass_dot_kg_s"),
        _ep("thruster", "thrust_force_n"), _ep("thruster", "thrust_force_b_x_n"),
        _ep("thruster", "thrust_factor"),
    ),
)
PROPULSION_PARAMETER_SET = ParameterSet(
    PROPULSION_PARAMETER_SET_ID, PROPULSION_ROOT_REF, {str(p.name): p.default for p in PROPULSION_PARAMETERS},
    source_refs=(
        "src/sat_sim/capabilities/subsystem.propulsion.unified_native.v1.yaml",
        "sat_sim.adapters.subsystem_propulsion_unified_native.PropulsionUnifiedNativeAdapter",
    ),
    applicability="A5R focused propulsion migration", confidence="engineering_demo",
)
PROPULSION_PROFILE = ModelImplementationProfile(
    PROPULSION_IMPLEMENTATION, PROPULSION_ROOT_REF, engine="basilisk", fidelity="unified_native",
    adapter_key=PROPULSION_ADAPTER_KEY,
    supported_outputs=("position_x_m", "velocity_x_m_s", "fuel_mass_kg", "thrust_force_n"),
    supported_effects=("nominal",),
    limitations=("Focused subsystem burn; runtime fault/degradation effects remain outside this capability.",),
    runtime_requirements=("Basilisk==2.11.0+satfix1",),
)
PROPULSION_OBJECT = OperationsObjectDefinition(
    PROPULSION_OBJECT_REF, "Propulsion operations object",
    properties=(
        ObjectPropertySpec(ObjectRef("spacecraft.propulsion.fuel_mass_kg"), DataType.NUMBER, unit="kg"),
        ObjectPropertySpec(ObjectRef("spacecraft.propulsion.velocity_x_m_s"), DataType.NUMBER, unit="m/s"),
        ObjectPropertySpec(ObjectRef("spacecraft.propulsion.thrust_active"), DataType.BOOLEAN),
    ),
    effects=(ObjectActionSpec(ObjectRef("spacecraft.propulsion.nominal")),),
    evidence=(ObjectEvidenceSpec(ObjectRef("spacecraft.propulsion.propellant_evidence"), DataType.NUMBER),),
)
PROPULSION_BINDINGS = BindingSet(
    PROPULSION_BINDING_SET_ID,
    properties=(
        PropertyBinding(lid("fuel-mass"), ObjectRef("spacecraft.propulsion.fuel_mass_kg"), ModelPath("propulsion.fuel_mass_kg")),
        PropertyBinding(lid("velocity"), ObjectRef("spacecraft.propulsion.velocity_x_m_s"), ModelPath("propulsion.velocity_x_m_s")),
        PropertyBinding(lid("thrust-active"), ObjectRef("spacecraft.propulsion.thrust_active"), ModelPath("propulsion.thrust_force_n")),
    ),
    effects=(EffectBinding(lid("nominal-effect"), ObjectRef("spacecraft.propulsion.nominal"), lid("nominal"), lid("propulsion")),),
    evidence=(EvidenceBinding(lid("propellant-evidence"), lid("propellant-evidence"), ModelPath("propulsion.fuel_mass_kg"), ObjectRef("spacecraft.propulsion.propellant_evidence")),),
)
PROPULSION_PROJECTION = CapabilityProjection(
    PROPULSION_PROJECTION_ID, PROPULSION_CAPABILITY_ID, PROPULSION_OBJECT_REF, PROPULSION_ROOT_REF,
    PROPULSION_GRAPH.graph_ref, PROPULSION_IMPLEMENTATION, PROPULSION_PARAMETER_SET_ID, PROPULSION_ADAPTER_KEY,
    exposed_parameters=tuple(ExposedParameter(name, name) for name in PROPULSION_PUBLIC_PARAMETERS),
    exposed_properties=tuple(item.ref for item in PROPULSION_OBJECT.properties),
    exposed_actions=(), exposed_effects=tuple(item.ref for item in PROPULSION_OBJECT.effects),
    evidence_refs=tuple(item.ref for item in PROPULSION_OBJECT.evidence),
    description="Execute the governed propulsion unified-native subsystem graph.",
)
PROPULSION_REQUIRED_OUTPUTS = (
    "propulsion.position_x_m", "propulsion.velocity_x_m_s", "propulsion.fuel_mass_kg",
    "propulsion.fuel_mass_dot_kg_s", "propulsion.thrust_force_n",
    "propulsion.thrust_force_b_x_n", "propulsion.thrust_factor",
)


EPS_BUNDLE = SubsystemAssetBundle(
    EPS_CAPABILITY_ID, EPS_ADAPTER_KEY, EPS_ROOT_DEFINITION, EPS_GRAPH, EPS_PARAMETER_SET,
    EPS_PROFILE, EPS_OBJECT, EPS_BINDINGS, EPS_PROJECTION, EPS_REQUIRED_OUTPUTS, "a5r_eps_diagnostics",
)
COMM_BUNDLE = SubsystemAssetBundle(
    COMM_CAPABILITY_ID, COMM_ADAPTER_KEY, COMM_ROOT_DEFINITION, COMM_GRAPH, COMM_PARAMETER_SET,
    COMM_PROFILE, COMM_OBJECT, COMM_BINDINGS, COMM_PROJECTION, COMM_REQUIRED_OUTPUTS, "a5r_comm_diagnostics",
)
THERMAL_BUNDLE = SubsystemAssetBundle(
    THERMAL_CAPABILITY_ID, THERMAL_ADAPTER_KEY, THERMAL_ROOT_DEFINITION, THERMAL_GRAPH, THERMAL_PARAMETER_SET,
    THERMAL_PROFILE, THERMAL_OBJECT, THERMAL_BINDINGS, THERMAL_PROJECTION, THERMAL_REQUIRED_OUTPUTS, "a5r_thermal_diagnostics",
)
PROPULSION_BUNDLE = SubsystemAssetBundle(
    PROPULSION_CAPABILITY_ID, PROPULSION_ADAPTER_KEY, PROPULSION_ROOT_DEFINITION, PROPULSION_GRAPH,
    PROPULSION_PARAMETER_SET, PROPULSION_PROFILE, PROPULSION_OBJECT, PROPULSION_BINDINGS,
    PROPULSION_PROJECTION, PROPULSION_REQUIRED_OUTPUTS, "a5r_propulsion_diagnostics",
)

SUBSYSTEM_BUNDLES = (EPS_BUNDLE, COMM_BUNDLE, THERMAL_BUNDLE, PROPULSION_BUNDLE)
BUNDLES_BY_ADAPTER_KEY = {bundle.adapter_key: bundle for bundle in SUBSYSTEM_BUNDLES}
BUNDLES_BY_CAPABILITY_ID = {bundle.capability_id: bundle for bundle in SUBSYSTEM_BUNDLES}


def build_registry() -> ModelAssetRegistry:
    registry = ModelAssetRegistry()
    definitions = (
        EPS_POWER_SOURCE_DEFINITION, EPS_PDU_DEFINITION, EPS_BUS_DEFINITION, EPS_BATTERY_DEFINITION, EPS_ROOT_DEFINITION,
        COMM_INSTRUMENT_DEFINITION, COMM_STORAGE_DEFINITION, COMM_TRANSMITTER_DEFINITION, COMM_ROOT_DEFINITION,
        THERMAL_ENV_DEFINITION, THERMAL_LOAD_DEFINITION, THERMAL_NETWORK_DEFINITION, THERMAL_HEATER_DEFINITION, THERMAL_ROOT_DEFINITION,
        PROPULSION_SCHEDULER_DEFINITION, PROPULSION_THRUSTER_DEFINITION, PROPULSION_TANK_DEFINITION,
        PROPULSION_DYNAMICS_DEFINITION, PROPULSION_ROOT_DEFINITION,
    )
    for definition in definitions:
        registry.register_definition(definition)
    for bundle in SUBSYSTEM_BUNDLES:
        registry.register_graph(bundle.graph)
        registry.register_parameter_set(bundle.parameter_set)
    registry.validate()
    return registry


SUBSYSTEM_ASSET_REGISTRY = build_registry()
SUBSYSTEM_IMPLEMENTATION_PROFILES = {
    str(bundle.implementation_id): bundle.implementation_profile for bundle in SUBSYSTEM_BUNDLES
}


def bundle_for_adapter_key(adapter_key: str) -> SubsystemAssetBundle:
    try:
        return BUNDLES_BY_ADAPTER_KEY[adapter_key]
    except KeyError as exc:
        raise KeyError(f"unknown A5R subsystem adapter key: {adapter_key}") from exc


def bundle_for_capability_id(capability_id: str) -> SubsystemAssetBundle:
    try:
        return BUNDLES_BY_CAPABILITY_ID[capability_id]
    except KeyError as exc:
        raise KeyError(f"unknown A5R subsystem capability: {capability_id}") from exc


def parameter_contracts(bundle: SubsystemAssetBundle) -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for item in bundle.root_definition.parameters:
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


__all__ = [name for name in globals() if name.isupper()] + [
    "SubsystemAssetBundle",
    "build_registry",
    "bundle_for_adapter_key",
    "bundle_for_capability_id",
    "parameter_contracts",
]
