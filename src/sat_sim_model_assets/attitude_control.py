"""Governed ReactionWheel -> ADCS -> Spacecraft assets for A3R."""
from __future__ import annotations

from sat_sim_kernel import (
    ActionBinding,
    BindingSet,
    ConnectionKind,
    DataType,
    EffectBinding,
    EffectKind,
    EffectSpec,
    EvidenceBinding,
    EvidenceSpec,
    ExpressionSpec,
    FieldMapping,
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
    TimeSemantics,
)
from sat_sim_operations import ObjectActionSpec, ObjectEvidenceSpec, ObjectPropertySpec, OperationsObjectDefinition
from sat_sim_projection import CapabilityProjection, ExposedParameter

from .contracts import ModelAssetRegistry, ParameterSet


def lid(value: str) -> LocalId:
    return LocalId(value)


def mref(value: str) -> ModelRef:
    return ModelRef.parse(value)


REACTION_WHEEL_REF = mref("component.reaction_wheel@1")
ATTITUDE_SENSOR_REF = mref("component.attitude_sensor@1")
ADCS_CONTROLLER_REF = mref("component.adcs_controller@1")
SPACECRAFT_BODY_REF = mref("component.spacecraft_body@1")
POWER_MANAGER_REF = mref("component.power_manager@1")
PAYLOAD_GATE_REF = mref("component.payload_gate@1")
ADCS_REF = mref("subsystem.adcs@1")
SPACECRAFT_REF = mref("spacecraft.demo_satellite@1")
ADCS_GRAPH_REF = ADCS_REF
SPACECRAFT_GRAPH_REF = SPACECRAFT_REF
BASILISK_RW_IMPLEMENTATION = ImplementationId.parse("component.reaction_wheel.basilisk@1")
PYTHON_RW_IMPLEMENTATION = ImplementationId.parse("component.reaction_wheel.python_reduced@1")
BASILISK_SPACECRAFT_IMPLEMENTATION = ImplementationId.parse("spacecraft.demo_satellite.basilisk_unified@1")
A3_ADAPTER_KEY = "basilisk.attitude_control_graph"
PARAMETER_SET_ID = lid("demo-satellite-attitude-v1")
BINDING_SET_ID = lid("attitude-control-bindings-v1")
PROJECTION_ID = lid("whole-spacecraft-attitude-projection-v1")
OBJECT_REF = ObjectRef("spacecraft.attitude_control")

REACTION_WHEEL_DEFINITION = ModelDefinition(
    model_ref=REACTION_WHEEL_REF,
    kind=ModelKind.COMPONENT,
    title="Reaction wheel set",
    description="Three-axis reaction-wheel set shared by Python and Basilisk implementations.",
    parameters=(
        ParameterSpec(lid("wheel_count"), DataType.INTEGER, default_is_set=True, default=3, minimum=1, maximum=4),
        ParameterSpec(lid("wheel_inertia_kg_m2"), DataType.NUMBER, unit="kg*m2", default_is_set=True, default=0.015, minimum=1.0e-6),
        ParameterSpec(lid("rw_max_torque_nm"), DataType.NUMBER, unit="N*m", default_is_set=True, default=0.2, minimum=0.0),
        ParameterSpec(lid("rw_max_speed_rad_s"), DataType.NUMBER, unit="rad/s", default_is_set=True, default=1047.2, minimum=0.0),
        ParameterSpec(lid("rw_damping_nms"), DataType.NUMBER, unit="N*m*s", default_is_set=True, default=0.0, minimum=0.0),
    ),
    inputs=(
        PortSpec(lid("motor_torque_command"), PortDirection.INPUT, DataType.VECTOR, unit="N*m", shape=(3,)),
    ),
    outputs=(
        PortSpec(lid("wheel_speed_rad_s"), PortDirection.OUTPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
        PortSpec(lid("body_torque_nm"), PortDirection.OUTPUT, DataType.VECTOR, unit="N*m", shape=(3,)),
        PortSpec(lid("electrical_power_w"), PortDirection.OUTPUT, DataType.NUMBER, unit="W"),
    ),
    states=(
        StateSpec(lid("wheel_speed_state"), DataType.VECTOR, unit="rad/s", shape=(3,), initial_value_is_set=True, initial_value=(0.0, 0.0, 0.0)),
    ),
    evidence=(
        EvidenceSpec(lid("wheel-speed-evidence"), ModelPath("reaction_wheels.wheel_speed_rad_s"), DataType.VECTOR, unit="rad/s"),
        EvidenceSpec(lid("command-torque-evidence"), ModelPath("reaction_wheels.motor_torque_command"), DataType.VECTOR, unit="N*m"),
    ),
    effects=(
        EffectSpec(lid("rw_jamming"), EffectKind.FAULT, parameters=(ParameterSpec(lid("wheel_index"), DataType.INTEGER, required=True, minimum=0, maximum=2),), target_patterns=("reaction_wheels",), evidence_ids=(lid("wheel-speed-evidence"),)),
        EffectSpec(lid("rw_motor_failure"), EffectKind.FAULT, parameters=(ParameterSpec(lid("wheel_index"), DataType.INTEGER, required=True, minimum=0, maximum=2),), target_patterns=("reaction_wheels",), evidence_ids=(lid("command-torque-evidence"),)),
        EffectSpec(lid("rw_friction_degradation"), EffectKind.DEGRADATION, target_patterns=("reaction_wheels",), evidence_ids=(lid("wheel-speed-evidence"),)),
        EffectSpec(lid("rw_speed_limit"), EffectKind.CONSTRAINT, parameters=(ParameterSpec(lid("wheel_index"), DataType.INTEGER, required=True, minimum=0, maximum=2),), target_patterns=("reaction_wheels",), evidence_ids=(lid("wheel-speed-evidence"),)),
    ),
    tags=("adcs", "actuator", "a3r"),
)

ATTITUDE_SENSOR_DEFINITION = ModelDefinition(
    model_ref=ATTITUDE_SENSOR_REF,
    kind=ModelKind.COMPONENT,
    title="Attitude sensing chain",
    inputs=(
        PortSpec(lid("true_attitude_mrp"), PortDirection.INPUT, DataType.VECTOR, shape=(3,)),
        PortSpec(lid("true_body_rate_rad_s"), PortDirection.INPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
    ),
    outputs=(
        PortSpec(lid("attitude_measurement_mrp"), PortDirection.OUTPUT, DataType.VECTOR, shape=(3,)),
        PortSpec(lid("body_rate_measurement_rad_s"), PortDirection.OUTPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
        PortSpec(lid("pointing_error_deg"), PortDirection.OUTPUT, DataType.NUMBER, unit="deg"),
    ),
)

ADCS_CONTROLLER_DEFINITION = ModelDefinition(
    model_ref=ADCS_CONTROLLER_REF,
    kind=ModelKind.COMPONENT,
    title="MRP feedback controller",
    parameters=(
        ParameterSpec(lid("controller_k"), DataType.NUMBER, default_is_set=True, default=3.5, minimum=0.0),
        ParameterSpec(lid("controller_p"), DataType.NUMBER, default_is_set=True, default=30.0, minimum=0.0),
    ),
    inputs=(
        PortSpec(lid("attitude_measurement_mrp"), PortDirection.INPUT, DataType.VECTOR, shape=(3,)),
        PortSpec(lid("body_rate_measurement_rad_s"), PortDirection.INPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
        PortSpec(lid("rw_speed_feedback_rad_s"), PortDirection.INPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
        PortSpec(lid("mode_command"), PortDirection.INPUT, DataType.STRING),
    ),
    outputs=(
        PortSpec(lid("motor_torque_command"), PortDirection.OUTPUT, DataType.VECTOR, unit="N*m", shape=(3,)),
    ),
)

SPACECRAFT_BODY_DEFINITION = ModelDefinition(
    model_ref=SPACECRAFT_BODY_REF,
    kind=ModelKind.COMPONENT,
    title="Rigid spacecraft body",
    parameters=(
        ParameterSpec(lid("spacecraft_mass_kg"), DataType.NUMBER, unit="kg", default_is_set=True, default=100.0, minimum=0.001),
        ParameterSpec(lid("initial_pointing_error_deg"), DataType.NUMBER, unit="deg", default_is_set=True, default=8.0, minimum=0.0, maximum=60.0),
    ),
    inputs=(PortSpec(lid("applied_torque_nm"), PortDirection.INPUT, DataType.VECTOR, unit="N*m", shape=(3,)),),
    outputs=(
        PortSpec(lid("attitude_mrp"), PortDirection.OUTPUT, DataType.VECTOR, shape=(3,)),
        PortSpec(lid("body_rate_rad_s"), PortDirection.OUTPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
    ),
)

POWER_MANAGER_DEFINITION = ModelDefinition(
    model_ref=POWER_MANAGER_REF,
    kind=ModelKind.COMPONENT,
    title="Power and safe-mode manager",
    parameters=(
        ParameterSpec(lid("initial_soc"), DataType.NUMBER, unit="ratio", default_is_set=True, default=0.62, minimum=0.0, maximum=1.0),
        ParameterSpec(lid("battery_capacity_wh"), DataType.NUMBER, unit="Wh", default_is_set=True, default=160.0, minimum=0.001),
    ),
    inputs=(
        PortSpec(lid("load_power_w"), PortDirection.INPUT, DataType.NUMBER, unit="W"),
        PortSpec(lid("safe_mode_request"), PortDirection.INPUT, DataType.BOOLEAN, time_semantics=TimeSemantics.EVENT),
    ),
    outputs=(
        PortSpec(lid("safe_mode_command"), PortDirection.OUTPUT, DataType.STRING),
        PortSpec(lid("battery_soc"), PortDirection.OUTPUT, DataType.NUMBER, unit="ratio"),
    ),
)

PAYLOAD_GATE_DEFINITION = ModelDefinition(
    model_ref=PAYLOAD_GATE_REF,
    kind=ModelKind.COMPONENT,
    title="Pointing and safe-mode payload gate",
    inputs=(
        PortSpec(lid("pointing_error_deg"), PortDirection.INPUT, DataType.NUMBER, unit="deg"),
        PortSpec(lid("mode_command"), PortDirection.INPUT, DataType.STRING),
    ),
    outputs=(PortSpec(lid("payload_active"), PortDirection.OUTPUT, DataType.BOOLEAN),),
)

ADCS_INTERFACE_DEFINITION = ModelDefinition(
    model_ref=ADCS_REF,
    kind=ModelKind.SUBSYSTEM,
    title="ADCS composite interface",
    inputs=(PortSpec(lid("mode_command"), PortDirection.INPUT, DataType.STRING),),
    outputs=(
        PortSpec(lid("pointing_error_deg"), PortDirection.OUTPUT, DataType.NUMBER, unit="deg"),
        PortSpec(lid("rw_speed_rad_s"), PortDirection.OUTPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
        PortSpec(lid("electrical_power_w"), PortDirection.OUTPUT, DataType.NUMBER, unit="W"),
    ),
)

SPACECRAFT_INTERFACE_DEFINITION = ModelDefinition(
    model_ref=SPACECRAFT_REF,
    kind=ModelKind.SPACECRAFT,
    title="Demo satellite attitude-control interface",
    parameters=(
        *SPACECRAFT_BODY_DEFINITION.parameters,
        *ADCS_CONTROLLER_DEFINITION.parameters,
        *REACTION_WHEEL_DEFINITION.parameters,
        *POWER_MANAGER_DEFINITION.parameters,
        ParameterSpec(lid("orbit_radius_m"), DataType.NUMBER, unit="m", default_is_set=True, default=7_000_000.0, minimum=6_478_000.0),
        ParameterSpec(lid("inclination_deg"), DataType.NUMBER, unit="deg", default_is_set=True, default=35.0, minimum=0.0, maximum=180.0),
        ParameterSpec(lid("solar_panel_area_m2"), DataType.NUMBER, unit="m2", default_is_set=True, default=2.5, minimum=0.001),
        ParameterSpec(lid("solar_efficiency"), DataType.NUMBER, unit="ratio", default_is_set=True, default=0.28, minimum=0.0, maximum=1.0),
        ParameterSpec(lid("bus_power_w"), DataType.NUMBER, unit="W", default_is_set=True, default=18.0, minimum=0.0),
        ParameterSpec(lid("adcs_power_w"), DataType.NUMBER, unit="W", default_is_set=True, default=12.0, minimum=0.0),
        ParameterSpec(lid("payload_power_w"), DataType.NUMBER, unit="W", default_is_set=True, default=38.0, minimum=0.0),
        ParameterSpec(lid("downlink_power_w"), DataType.NUMBER, unit="W", default_is_set=True, default=16.0, minimum=0.0),
        ParameterSpec(lid("payload_data_rate_bps"), DataType.NUMBER, unit="bit/s", default_is_set=True, default=2_500_000.0, minimum=0.0),
        ParameterSpec(lid("downlink_rate_bps"), DataType.NUMBER, unit="bit/s", default_is_set=True, default=1_500_000.0, minimum=0.0),
        ParameterSpec(lid("storage_capacity_bits"), DataType.NUMBER, unit="bit", default_is_set=True, default=6_000_000_000.0, minimum=1.0),
        ParameterSpec(lid("payload_max_pointing_error_deg"), DataType.NUMBER, unit="deg", default_is_set=True, default=20.0, minimum=0.0),
    ),
    inputs=(PortSpec(lid("safe_mode_request"), PortDirection.INPUT, DataType.BOOLEAN, time_semantics=TimeSemantics.EVENT),),
    outputs=(
        PortSpec(lid("pointing_error_deg"), PortDirection.OUTPUT, DataType.NUMBER, unit="deg"),
        PortSpec(lid("rw_speed_rad_s"), PortDirection.OUTPUT, DataType.VECTOR, unit="rad/s", shape=(3,)),
        PortSpec(lid("battery_soc"), PortDirection.OUTPUT, DataType.NUMBER, unit="ratio"),
        PortSpec(lid("payload_active"), PortDirection.OUTPUT, DataType.BOOLEAN),
    ),
)

ADCS_GRAPH = ModelGraphDefinition(
    graph_ref=ADCS_GRAPH_REF,
    kind=ModelKind.SUBSYSTEM,
    title="ADCS feedback model graph",
    nodes=(
        ModelNode(lid("body"), SPACECRAFT_BODY_REF),
        ModelNode(lid("sensor"), ATTITUDE_SENSOR_REF),
        ModelNode(lid("controller"), ADCS_CONTROLLER_REF),
        ModelNode(lid("reaction_wheels"), REACTION_WHEEL_REF),
    ),
    connections=(
        ModelConnection(PortEndpoint(lid("body"), lid("attitude_mrp")), PortEndpoint(lid("sensor"), lid("true_attitude_mrp")), feedback=True),
        ModelConnection(PortEndpoint(lid("body"), lid("body_rate_rad_s")), PortEndpoint(lid("sensor"), lid("true_body_rate_rad_s")), feedback=True),
        ModelConnection(PortEndpoint(lid("sensor"), lid("attitude_measurement_mrp")), PortEndpoint(lid("controller"), lid("attitude_measurement_mrp"))),
        ModelConnection(PortEndpoint(lid("sensor"), lid("body_rate_measurement_rad_s")), PortEndpoint(lid("controller"), lid("body_rate_measurement_rad_s"))),
        ModelConnection(PortEndpoint(lid("controller"), lid("motor_torque_command")), PortEndpoint(lid("reaction_wheels"), lid("motor_torque_command"))),
        ModelConnection(PortEndpoint(lid("reaction_wheels"), lid("wheel_speed_rad_s")), PortEndpoint(lid("controller"), lid("rw_speed_feedback_rad_s")), feedback=True),
        ModelConnection(PortEndpoint(lid("reaction_wheels"), lid("body_torque_nm")), PortEndpoint(lid("body"), lid("applied_torque_nm"))),
    ),
    exposed_inputs=(PortEndpoint(lid("controller"), lid("mode_command")),),
    exposed_outputs=(
        PortEndpoint(lid("sensor"), lid("pointing_error_deg")),
        PortEndpoint(lid("reaction_wheels"), lid("wheel_speed_rad_s")),
        PortEndpoint(lid("reaction_wheels"), lid("electrical_power_w")),
    ),
)

SPACECRAFT_GRAPH = ModelGraphDefinition(
    graph_ref=SPACECRAFT_GRAPH_REF,
    kind=ModelKind.SPACECRAFT,
    title="Demo satellite model graph",
    nodes=(
        ModelNode(lid("adcs"), ADCS_REF),
        ModelNode(lid("power_manager"), POWER_MANAGER_REF),
        ModelNode(lid("payload_gate"), PAYLOAD_GATE_REF),
    ),
    connections=(
        ModelConnection(PortEndpoint(lid("adcs"), lid("electrical_power_w")), PortEndpoint(lid("power_manager"), lid("load_power_w")), kind=ConnectionKind.RESOURCE),
        ModelConnection(PortEndpoint(lid("power_manager"), lid("safe_mode_command")), PortEndpoint(lid("adcs"), lid("mode_command")), feedback=True),
        ModelConnection(PortEndpoint(lid("adcs"), lid("pointing_error_deg")), PortEndpoint(lid("payload_gate"), lid("pointing_error_deg"))),
        ModelConnection(PortEndpoint(lid("power_manager"), lid("safe_mode_command")), PortEndpoint(lid("payload_gate"), lid("mode_command"))),
    ),
    exposed_inputs=(PortEndpoint(lid("power_manager"), lid("safe_mode_request")),),
    exposed_outputs=(
        PortEndpoint(lid("adcs"), lid("pointing_error_deg")),
        PortEndpoint(lid("adcs"), lid("rw_speed_rad_s")),
        PortEndpoint(lid("power_manager"), lid("battery_soc")),
        PortEndpoint(lid("payload_gate"), lid("payload_active")),
    ),
)

PARAMETER_SET = ParameterSet(
    parameter_set_id=PARAMETER_SET_ID,
    model_ref=SPACECRAFT_REF,
    values={
        "spacecraft_mass_kg": 100.0,
        "initial_pointing_error_deg": 8.0,
        "controller_k": 3.5,
        "controller_p": 30.0,
        "wheel_count": 3,
        "wheel_inertia_kg_m2": 0.015,
        "rw_max_torque_nm": 0.2,
        "rw_max_speed_rad_s": 1047.2,
        "rw_damping_nms": 0.0,
        "initial_soc": 0.62,
        "battery_capacity_wh": 160.0,
        "orbit_radius_m": 7_000_000.0,
        "inclination_deg": 35.0,
        "solar_panel_area_m2": 2.5,
        "solar_efficiency": 0.28,
        "bus_power_w": 18.0,
        "adcs_power_w": 12.0,
        "payload_power_w": 38.0,
        "downlink_power_w": 16.0,
        "payload_data_rate_bps": 2_500_000.0,
        "downlink_rate_bps": 1_500_000.0,
        "storage_capacity_bits": 6_000_000_000.0,
        "payload_max_pointing_error_deg": 20.0,
    },
    source_refs=(
        "components.reaction_wheel.schemas.ReactionWheelSpec",
        "sat_sim.bsk_engine.unified_native.UnifiedNativeRuntime",
        "parameters/data/demo_parameter_registry_v1.json",
    ),
    applicability="demo satellite A3R vertical slice",
    confidence="engineering_demo",
    notes=("Values preserve the v0.5.7.5 unified-native defaults; they are not flight calibration data.",),
)

BASILISK_RW_PROFILE = ModelImplementationProfile(
    implementation_id=BASILISK_RW_IMPLEMENTATION,
    model_ref=REACTION_WHEEL_REF,
    engine="basilisk",
    fidelity="native_dynamics",
    adapter_key=A3_ADAPTER_KEY,
    supported_inputs=("motor_torque_command",),
    supported_outputs=("wheel_speed_rad_s", "body_torque_nm", "electrical_power_w"),
    supported_effects=("rw_jamming", "rw_motor_failure", "rw_friction_degradation", "rw_speed_limit"),
    runtime_requirements=("Basilisk==2.11.0+satfix1",),
)

PYTHON_RW_PROFILE = ModelImplementationProfile(
    implementation_id=PYTHON_RW_IMPLEMENTATION,
    model_ref=REACTION_WHEEL_REF,
    engine="python",
    fidelity="reduced_order",
    adapter_key="python.reaction_wheel_reduced",
    supported_inputs=("motor_torque_command",),
    supported_outputs=("wheel_speed_rad_s", "body_torque_nm"),
    supported_effects=("rw_jamming", "rw_motor_failure", "rw_speed_limit"),
    limitations=("Does not reproduce the full Basilisk spacecraft/control message graph.",),
)

BASILISK_SPACECRAFT_PROFILE = ModelImplementationProfile(
    implementation_id=BASILISK_SPACECRAFT_IMPLEMENTATION,
    model_ref=SPACECRAFT_REF,
    engine="basilisk",
    fidelity="unified_native",
    adapter_key=A3_ADAPTER_KEY,
    supported_inputs=("safe_mode_request",),
    supported_outputs=("pointing_error_deg", "rw_speed_rad_s", "battery_soc", "payload_active"),
    supported_effects=("rw_jamming", "rw_motor_failure", "rw_friction_degradation", "rw_speed_limit", "power_safe_mode_threshold"),
    runtime_requirements=("Basilisk==2.11.0+satfix1",),
)

ATTITUDE_CONTROL_OBJECT = OperationsObjectDefinition(
    object_ref=OBJECT_REF,
    title="Spacecraft attitude-control operations object",
    properties=(
        ObjectPropertySpec(ObjectRef("spacecraft.attitude_control.pointing_status"), DataType.ENUM),
        ObjectPropertySpec(ObjectRef("spacecraft.attitude_control.rw_speed"), DataType.VECTOR, unit="rad/s"),
    ),
    actions=(ObjectActionSpec(ObjectRef("spacecraft.attitude_control.enter_safe_mode"), ("start_s", "end_s")),),
    effects=(ObjectActionSpec(ObjectRef("spacecraft.attitude_control.inject_rw_jamming"), ("wheel_index", "start_s", "end_s")),),
    evidence=(ObjectEvidenceSpec(ObjectRef("spacecraft.attitude_control.pointing_evidence"), DataType.BOOLEAN),),
)

ATTITUDE_CONTROL_BINDINGS = BindingSet(
    binding_set_id=BINDING_SET_ID,
    properties=(
        PropertyBinding(lid("pointing-status"), ObjectRef("spacecraft.attitude_control.pointing_status"), ModelPath("adcs.pointing_error_deg"), ExpressionSpec("'STABLE' if value < 0.5 else 'UNSTABLE'")),
        PropertyBinding(lid("rw-speed"), ObjectRef("spacecraft.attitude_control.rw_speed"), ModelPath("adcs.rw_speed_rad_s")),
    ),
    actions=(
        ActionBinding(lid("enter-safe-mode"), ObjectRef("spacecraft.attitude_control.enter_safe_mode"), ModelPath("power_manager.safe_mode_request"), value_expression=ExpressionSpec("True")),
    ),
    effects=(
        EffectBinding(
            lid("inject-rw-jamming"),
            ObjectRef("spacecraft.attitude_control.inject_rw_jamming"),
            lid("rw_jamming"),
            lid("reaction_wheels"),
            parameter_mappings=(FieldMapping(lid("wheel_index"), lid("wheel_index")),),
        ),
    ),
    evidence=(
        EvidenceBinding(lid("pointing-evidence"), lid("pointing-stable"), ModelPath("adcs.pointing_error_deg"), ObjectRef("spacecraft.attitude_control.pointing_evidence"), ExpressionSpec("value < 0.5")),
    ),
)

ATTITUDE_CONTROL_PROJECTION = CapabilityProjection(
    projection_id=PROJECTION_ID,
    capability_id="whole_spacecraft.unified_native.v1",
    object_ref=OBJECT_REF,
    model_ref=SPACECRAFT_REF,
    graph_ref=SPACECRAFT_GRAPH_REF,
    implementation_id=BASILISK_SPACECRAFT_IMPLEMENTATION,
    parameter_set_id=PARAMETER_SET_ID,
    adapter_key=A3_ADAPTER_KEY,
    exposed_parameters=tuple(
        ExposedParameter(name, name)
        for name in (
            "initial_pointing_error_deg", "controller_k", "controller_p", "rw_max_torque_nm",
            "initial_soc", "battery_capacity_wh", "orbit_radius_m", "inclination_deg",
            "solar_panel_area_m2", "solar_efficiency", "bus_power_w", "adcs_power_w",
            "payload_power_w", "downlink_power_w", "payload_data_rate_bps",
            "downlink_rate_bps", "storage_capacity_bits", "payload_max_pointing_error_deg",
        )
    ),
    exposed_properties=tuple(item.ref for item in ATTITUDE_CONTROL_OBJECT.properties),
    exposed_actions=tuple(item.ref for item in ATTITUDE_CONTROL_OBJECT.actions),
    exposed_effects=tuple(item.ref for item in ATTITUDE_CONTROL_OBJECT.effects),
    evidence_refs=tuple(item.ref for item in ATTITUDE_CONTROL_OBJECT.evidence),
)


def build_registry() -> ModelAssetRegistry:
    registry = ModelAssetRegistry()
    for definition in (
        REACTION_WHEEL_DEFINITION,
        ATTITUDE_SENSOR_DEFINITION,
        ADCS_CONTROLLER_DEFINITION,
        SPACECRAFT_BODY_DEFINITION,
        POWER_MANAGER_DEFINITION,
        PAYLOAD_GATE_DEFINITION,
        ADCS_INTERFACE_DEFINITION,
        SPACECRAFT_INTERFACE_DEFINITION,
    ):
        registry.register_definition(definition)
    registry.register_graph(ADCS_GRAPH)
    registry.register_graph(SPACECRAFT_GRAPH)
    registry.register_parameter_set(PARAMETER_SET)
    registry.validate()
    return registry


ASSET_REGISTRY = build_registry()
IMPLEMENTATION_PROFILES = {
    str(profile.implementation_id): profile
    for profile in (BASILISK_RW_PROFILE, PYTHON_RW_PROFILE, BASILISK_SPACECRAFT_PROFILE)
}


def parameter_contracts() -> dict[str, dict[str, object]]:
    out: dict[str, dict[str, object]] = {}
    for item in SPACECRAFT_INTERFACE_DEFINITION.parameters:
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


__all__ = [name for name in globals() if name.isupper()] + ["ASSET_REGISTRY", "build_registry", "parameter_contracts"]
