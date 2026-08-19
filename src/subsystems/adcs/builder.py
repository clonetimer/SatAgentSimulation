"""ADCS builder with whole-spacecraft-oriented primary entrypoints."""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Optional

from components.fault_spec import FaultSpec
from components.reaction_wheel.faults import RWFaultType
from components.reaction_wheel.builder import (
    attach_reaction_wheels_to_spacecraft,
    build_reaction_wheel_bundle,
    write_rw_torque_message,
)
from components.reaction_wheel.degradation import apply_native_friction_factor_to_specs
from components.reaction_wheel.schemas import ReactionWheelSpec
from components.reaction_wheel.faults import apply_reaction_wheel_spec_faults
from components.mtb.builder import build_mtb_effector_bundle, attach_mtb_to_spacecraft
from components.cmg.builder import build_nominal_cmg_config, build_vscmg_bundle, attach_vscmg_to_spacecraft, write_vscmg_array_torque_message, VscmgSpec
from components.imu.builder import build_nominal_imu_config, build_imu_sensor
from components.star_tracker.builder import build_nominal_star_tracker_config, build_star_tracker
from components.sun_sensor.builder import build_nominal_sun_sensor_config, build_coarse_sun_sensor
from components.magnetometer.builder import build_nominal_magnetometer_config, build_magnetometer

from .schemas import (
    ADCSBasiliskConfig,
    AdcsConfig,
    AdcsControlConfig,
    AdcsControlState,
    AdcsActuatorConfig,
    AdcsActuatorState,
    AdcsSensorConfig,
    AdcsSensorState,
    AdcsCommandChainConfig,
    AdcsCommandChainState,
    AdcsCommandChainInput,
    MomentumDumpConfig,
    MomentumDumpResult,
    MagneticDetumbleConfig,
    MagneticDetumbleResult,
    RwOnlyClosedLoopConfig,
    RwWheelConfig,
)
from .model import (
    step_adcs_control,
    run_control_profile,
    summarize_control_profile,
    step_adcs_actuators,
    run_actuator_profile,
    summarize_actuator_profile,
    step_adcs_sensors,
    simulate_adcs_sensor_profile,
    step_adcs_command_chain,
    run_adcs_command_chain_profile,
    summarize_command_chain_profile,
    run_momentum_dump,
    run_magnetic_detumble,
)
from .degradation import ADCSDegradation, apply_adcs_degradation


@dataclass(frozen=True)
class ADCSClosedLoopAssemblyGraph:
    rw_closed_loop: Any
    note: str


def build_nominal_adcs_config(degradation: Optional[ADCSDegradation] = None) -> AdcsConfig:
    cfg = AdcsConfig(
        rw_duration_s=300.0,
        rw_dyn_step_s=0.2,
        rw_fsw_step_s=0.2,
        rw_sample_s=2.0,
        mtb_duration_s=120.0,
        mtb_control_step_s=2.0,
        mtb_dynamics_step_s=1.0,
        mtb_sample_s=4.0,
    )

    if degradation is not None:
        cfg = apply_adcs_degradation(cfg, degradation)

    return cfg


def apply_adcs_config_faults(cfg: AdcsConfig, fault_specs: list[FaultSpec]) -> AdcsConfig:
    """Delegate component-fault interpretation to ``subsystems.adcs.faults``."""

    from .faults import apply_adcs_config_faults as _apply_adcs_config_faults

    return _apply_adcs_config_faults(cfg, fault_specs)


def _wheel_config_to_component_spec(wheel: RwWheelConfig) -> ReactionWheelSpec:
    return ReactionWheelSpec(
        axis_b=tuple(wheel.axis_b),
        model=str(wheel.model),
        max_momentum_nms=float(wheel.max_momentum_nms),
        initial_speed_rpm=float(wheel.initial_speed_rpm),
        wheel_js=float(wheel.wheel_js),
        u_max_nm=float(wheel.u_max_nm),
        omega_max_rad_s=float(wheel.omega_max_rad_s),
        use_min_torque=bool(wheel.use_min_torque),
        u_min_nm=float(wheel.u_min_nm),
        max_power_w=float(wheel.max_power_w),
        position_b_m=tuple(wheel.position_b_m),
        label=wheel.label,
        use_rw_friction=bool(wheel.use_rw_friction),
        fCoulomb=float(wheel.fCoulomb),
        fStatic=float(wheel.fStatic),
        betaStatic=float(wheel.betaStatic),
        cViscous=float(wheel.cViscous),
    )


def _component_spec_to_wheel_config(spec: ReactionWheelSpec) -> RwWheelConfig:
    return RwWheelConfig(
        model=str(spec.model),
        axis_b=tuple(spec.axis_b),
        max_momentum_nms=float(spec.max_momentum_nms),
        initial_speed_rpm=float(spec.initial_speed_rpm),
        wheel_js=float(spec.wheel_js),
        u_max_nm=float(spec.u_max_nm),
        omega_max_rad_s=float(spec.omega_max_rad_s),
        use_min_torque=bool(spec.use_min_torque),
        u_min_nm=float(spec.u_min_nm),
        max_power_w=float(spec.max_power_w),
        position_b_m=tuple(spec.position_b_m),
        label=spec.label,
        use_rw_friction=bool(spec.use_rw_friction),
        fCoulomb=float(spec.fCoulomb),
        fStatic=float(spec.fStatic),
        betaStatic=float(spec.betaStatic),
        cViscous=float(spec.cViscous),
    )


def build_adcs_reaction_wheel_bundle(
    config: RwOnlyClosedLoopConfig | None = None,
    *,
    model_tag: str = "adcsClosedLoopRWCluster",
):
    """Build the ADCS RW cluster through the component-owned constructor.

    This is the public subsystem boundary for higher-level assembly code.  It
    converts ADCS wheel configuration to component schemas, while the component
    package remains the sole owner of ``rwFactory`` and native field mapping.
    """

    cfg = config or RwOnlyClosedLoopConfig()
    return build_reaction_wheel_bundle(
        model_tag=model_tag,
        wheel_specs=tuple(_wheel_config_to_component_spec(wheel) for wheel in cfg.wheels),
    )


def build_adcs_closed_loop_config(
    config: AdcsConfig | None = None,
    *,
    fault_specs: list[FaultSpec] | tuple[FaultSpec, ...] = (),
    base_config: RwOnlyClosedLoopConfig | None = None,
) -> RwOnlyClosedLoopConfig:
    """Compose the ADCS closed-loop config from component-owned RW mappings.

    Higher layers call this subsystem factory rather than reconstructing wheel
    specs or friction mappings themselves.
    """

    adcs_cfg = config or build_nominal_adcs_config()
    base = base_config or RwOnlyClosedLoopConfig()
    component_specs = tuple(_wheel_config_to_component_spec(wheel) for wheel in base.wheels)
    component_specs = apply_native_friction_factor_to_specs(component_specs, float(adcs_cfg.rw_friction_factor))
    component_specs = apply_reaction_wheel_spec_faults(component_specs, tuple(fault_specs))
    return replace(
        base,
        dyn_step_s=float(adcs_cfg.rw_dyn_step_s),
        fsw_step_s=float(adcs_cfg.rw_fsw_step_s),
        sample_s=float(adcs_cfg.rw_sample_s),
        control_p=max(1.0, 25.0 / (1.0 + max(0.0, float(adcs_cfg.sensor_noise_factor)))),
        wheels=tuple(_component_spec_to_wheel_config(spec) for spec in component_specs),
    )


def _matrix_column(vec: tuple[float, float, float]) -> list[list[float]]:
    return [[float(vec[0])], [float(vec[1])], [float(vec[2])]]


def _matrix_3x3(flat: tuple[float, ...]) -> list[list[float]]:
    if len(flat) != 9:
        raise ValueError("spacecraft inertia must have 9 elements")
    return [
        [float(flat[0]), float(flat[1]), float(flat[2])],
        [float(flat[3]), float(flat[4]), float(flat[5])],
        [float(flat[6]), float(flat[7]), float(flat[8])],
    ]


def configure_adcs_spacecraft_hub(spacecraft_object: Any, config: Any, *, model_tag: str = "adcsClosedLoopSpacecraft") -> Any:
    """Configure a Basilisk spacecraft hub from ADCS closed-loop config values.

    This helper is intentionally side-effect-only on the provided spacecraft
    object.  Standalone ADCS builds may create the spacecraft locally, while
    whole-spacecraft builds should create the central spacecraft bus in
    ``whole_spacecraft.builder`` and then attach ADCS to that object.
    """

    cfg = config or RwOnlyClosedLoopConfig()
    spacecraft_object.ModelTag = model_tag
    spacecraft_object.hub.mHub = float(cfg.spacecraft_mass_kg)
    spacecraft_object.hub.r_BcB_B = [[0.0], [0.0], [0.0]]
    spacecraft_object.hub.IHubPntBc_B = _matrix_3x3(cfg.spacecraft_inertia_kg_m2)
    spacecraft_object.hub.sigma_BNInit = _matrix_column(cfg.initial_sigma_bn)
    spacecraft_object.hub.omega_BN_BInit = _matrix_column(cfg.initial_omega_bn_b_rad_s)
    return spacecraft_object


def attach_adcs_to_spacecraft(
    spacecraft_object: Any,
    config: Any = None,
    *,
    fusion_config: Any | None = None,
    environment_graph: Any | None = None,
) -> ADCSClosedLoopAssemblyGraph:
    """Attach a closed-loop ADCS assembly to an existing spacecraft object.

    Ownership of the spacecraft remains with the caller.  This is the preferred
    entry point for whole-spacecraft assembly because propulsion, environment,
    and ADCS can all be attached to the same central dynamics object.
    """

    from types import SimpleNamespace

    from Basilisk.architecture import messaging
    from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque
    from Basilisk.utilities import macros

    from .sensor_estimate import SensorFusionConfig, StarTrackerImuFusionEstimator

    cfg = config or RwOnlyClosedLoopConfig()
    if spacecraft_object is None:
        raise ValueError("spacecraft_object must be provided for ADCS attach mode")

    rw_bundle = build_adcs_reaction_wheel_bundle(
        cfg,
        model_tag="adcsClosedLoopRWCluster",
    )
    rw_bundle.factory.addToSpacecraft("adcsClosedLoopRWCluster", rw_bundle.effector, spacecraft_object)

    imu = build_imu_sensor(
        model_tag="adcsClosedLoopImu",
        sc_state_msg=spacecraft_object.scStateOutMsg,
    )

    st = build_star_tracker(
        model_tag="adcsClosedLoopStarTracker",
        sc_state_msg=spacecraft_object.scStateOutMsg,
    )

    base_fusion_cfg = fusion_config or SensorFusionConfig()
    fusion_cfg = replace(
        base_fusion_cfg,
        duration_s=float(cfg.duration_s),
        dyn_step_s=float(cfg.dyn_step_s),
        fsw_step_s=float(cfg.fsw_step_s),
        sample_s=float(cfg.sample_s),
        rw_config=cfg,
    )

    if environment_graph is not None and getattr(environment_graph, "sun_msg", None) is not None:
        sun_msg = environment_graph.sun_msg
    else:
        sun_payload = messaging.SpicePlanetStateMsgPayload()
        sun_payload.PositionVector = [float(value) for value in fusion_cfg.sun_position_n_m]
        sun_msg = messaging.SpicePlanetStateMsg().write(sun_payload)

    if environment_graph is not None and getattr(environment_graph, "eclipse_msg", None) is not None:
        eclipse_msg = environment_graph.eclipse_msg
    else:
        eclipse_payload = messaging.EclipseMsgPayload()
        eclipse_payload.illuminationFactor = 1.0
        eclipse_msg = messaging.EclipseMsg().write(eclipse_payload)

    css = build_coarse_sun_sensor(
        model_tag="adcsClosedLoopCss",
        sc_state_msg=spacecraft_object.scStateOutMsg,
        sun_in_msg=sun_msg,
        eclipse_in_msg=eclipse_msg,
        fov_rad=3.141592653589793,
    )

    if environment_graph is not None and getattr(environment_graph, "mag_field_msg", None) is not None:
        mag_msg = environment_graph.mag_field_msg
    else:
        mag_payload = messaging.MagneticFieldMsgPayload()
        mag_payload.magField_N = [float(value) for value in fusion_cfg.magnetic_field_n_t]
        mag_msg = messaging.MagneticFieldMsg().write(mag_payload)
    tam = build_magnetometer(
        model_tag="adcsClosedLoopMagnetometer",
        sc_state_msg=spacecraft_object.scStateOutMsg,
        mag_field_msg=mag_msg,
    )

    estimator = StarTrackerImuFusionEstimator(fusion_cfg, model_tag="adcsClosedLoopStarTrackerImuFusionEstimator")
    estimator.starTrackerInMsg.subscribeTo(st.sensorOutMsg)
    estimator.imuInMsg.subscribeTo(imu.sensorOutMsg)
    estimator.scStateInMsg.subscribeTo(spacecraft_object.scStateOutMsg)

    inertial_guidance = inertial3D.inertial3D()
    inertial_guidance.ModelTag = "adcsClosedLoopInertial3D"
    inertial_guidance.sigma_R0N = list(cfg.target_sigma_rn)

    attitude_error = attTrackingError.attTrackingError()
    attitude_error.ModelTag = "adcsClosedLoopAttTrackingError"
    attitude_error.attRefInMsg.subscribeTo(inertial_guidance.attRefOutMsg)
    attitude_error.attNavInMsg.subscribeTo(estimator.attOutMsg)

    vehicle_config = messaging.VehicleConfigMsgPayload(ISCPntB_B=list(cfg.spacecraft_inertia_kg_m2))
    vehicle_config_msg = messaging.VehicleConfigMsg().write(vehicle_config)

    controller = mrpFeedback.mrpFeedback()
    controller.ModelTag = "adcsClosedLoopMrpFeedback"
    controller.K = float(cfg.control_k)
    controller.P = float(cfg.control_p)
    controller.Ki = float(cfg.control_ki)
    controller.integralLimit = float(cfg.integral_limit)
    controller.guidInMsg.subscribeTo(attitude_error.attGuidOutMsg)
    controller.vehConfigInMsg.subscribeTo(vehicle_config_msg)
    controller.rwParamsInMsg.subscribeTo(rw_bundle.config_msg)
    rw_feedback_payload = messaging.RWSpeedMsgPayload()
    rw_feedback_speeds = list(rw_feedback_payload.wheelSpeeds)
    for idx, wheel in enumerate(cfg.wheels):
        rw_feedback_speeds[idx] = float(wheel.initial_speed_rpm) * 2.0 * 3.141592653589793 / 60.0
    rw_feedback_payload.wheelSpeeds = rw_feedback_speeds
    rw_feedback_speed_msg = messaging.RWSpeedMsg().write(rw_feedback_payload)
    controller.rwSpeedsInMsg.subscribeTo(rw_feedback_speed_msg)

    rw_torque = rwMotorTorque.rwMotorTorque()
    rw_torque.ModelTag = "adcsClosedLoopRwMotorTorque"
    rw_torque.controlAxes_B = [1.0, 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0]
    rw_torque.rwParamsInMsg.subscribeTo(rw_bundle.config_msg)
    rw_torque.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg)
    rw_bundle.effector.rwMotorCmdInMsg.subscribeTo(rw_torque.rwMotorTorqueOutMsg)

    sample_time = macros.sec2nano(float(cfg.sample_s))
    attitude_log = attitude_error.attGuidOutMsg.recorder(sample_time)
    rate_log = attitude_error.attGuidOutMsg.recorder(sample_time)
    rw_speed_log = rw_bundle.effector.rwSpeedOutMsg.recorder(sample_time)
    rw_motor_torque_log = rw_torque.rwMotorTorqueOutMsg.recorder(sample_time)

    closed_loop = SimpleNamespace(
        spacecraft=spacecraft_object,
        rw_state_effector=rw_bundle.effector,
        rw_config_msg=rw_bundle.config_msg,
        rw_bundle=rw_bundle,
        imu=imu,
        star_tracker=st,
        css=css,
        magnetometer=tam,
        estimator=estimator,
        inertial_guidance=inertial_guidance,
        attitude_error=attitude_error,
        controller=controller,
        vehicle_config_msg=vehicle_config_msg,
        rw_motor_torque=rw_torque,
        attitude_log=attitude_log,
        rate_log=rate_log,
        rw_speed_log=rw_speed_log,
        rw_motor_torque_log=rw_motor_torque_log,
        rw_feedback_speed_msg=rw_feedback_speed_msg,
        environment_graph=environment_graph,
        sun_msg=sun_msg,
        eclipse_msg=eclipse_msg,
        mag_msg=mag_msg,
        config=cfg,
    )
    return ADCSClosedLoopAssemblyGraph(
        rw_closed_loop=closed_loop,
        note="ADCS closed-loop graph attached to caller-owned spacecraft.",
    )


def build_adcs_closed_loop_graph(config: Any = None) -> ADCSClosedLoopAssemblyGraph:
    """Build a standalone closed-loop ADCS graph with an ADCS-owned spacecraft.

    This remains for subsystem-level and backward-compatible use.  Whole-
    spacecraft assembly should call :func:`attach_adcs_to_spacecraft` with a
    central spacecraft object owned by ``whole_spacecraft.builder``.
    """

    from Basilisk.simulation import spacecraft

    cfg = config or RwOnlyClosedLoopConfig()
    sc_object = spacecraft.Spacecraft()
    configure_adcs_spacecraft_hub(sc_object, cfg, model_tag="adcsClosedLoopSpacecraft")
    graph = attach_adcs_to_spacecraft(sc_object, cfg)
    return ADCSClosedLoopAssemblyGraph(
        rw_closed_loop=graph.rw_closed_loop,
        note="Standalone ADCS graph with ADCS-owned spacecraft; whole-spacecraft should use attach_adcs_to_spacecraft.",
    )

def attach_adcs_closed_loop_graph(sim: Any, sim_task_name: str, fsw_task_name: str, graph: ADCSClosedLoopAssemblyGraph) -> None:
    """Attach a closed-loop ADCS graph to existing whole-spacecraft tasks."""

    if graph.rw_closed_loop is None:
        raise RuntimeError("ADCS closed-loop graph is not built; got placeholder rw_closed_loop=None")
    cl = graph.rw_closed_loop
    for model in (cl.spacecraft, cl.rw_state_effector, cl.imu, cl.star_tracker, cl.css, cl.magnetometer):
        sim.AddModelToTask(sim_task_name, model)
    for model in (cl.estimator, cl.inertial_guidance, cl.attitude_error, cl.controller, cl.rw_motor_torque):
        sim.AddModelToTask(fsw_task_name, model)
    for recorder in (cl.attitude_log, cl.rate_log, cl.rw_speed_log, cl.rw_motor_torque_log):
        sim.AddModelToTask(fsw_task_name, recorder)


def build_adcs_basilisk_assembly_graph(*args: Any, **kwargs: Any) -> Any:
    from components.mtb.builder import build_mtb_effector_bundle

    rw_specs = kwargs.get("rw_specs")
    mtb_specs = kwargs.get("mtb_specs")
    rw_model_tag = kwargs.get("rw_model_tag", "adcsReactionWheelCluster")
    mtb_model_tag = kwargs.get("mtb_model_tag", "adcsMtbEffector")
    magnetic_field_t = kwargs.get("magnetic_field_t", (2.0e-5, -1.0e-5, 3.0e-5))
    rw_bundle = build_reaction_wheel_bundle(model_tag=rw_model_tag, wheel_specs=rw_specs)
    mtb_bundle = build_mtb_effector_bundle(model_tag=mtb_model_tag, specs=mtb_specs, magnetic_field_t=magnetic_field_t)

    @dataclass(frozen=True)
    class _LegacyADCSBasiliskAssemblyGraph:
        rw_bundle: Any
        mtb_bundle: Any
        note: str

    return _LegacyADCSBasiliskAssemblyGraph(
        rw_bundle=rw_bundle,
        mtb_bundle=mtb_bundle,
        note="Legacy actuation-side ADCS assembly graph; not used by whole-spacecraft main chain.",
    )


def attach_adcs_basilisk_graph_to_spacecraft(spacecraft: Any, sim: Any, task_name: str, graph: Any, *, rw_cluster_name: str = "adcsReactionWheelCluster") -> None:
    graph.rw_bundle.factory.addToSpacecraft(rw_cluster_name, graph.rw_bundle.effector, spacecraft)
    spacecraft.addDynamicEffector(graph.mtb_bundle.effector)
    sim.AddModelToTask(task_name, graph.rw_bundle.effector)
    sim.AddModelToTask(task_name, graph.mtb_bundle.effector)


def build_nominal_adcs_control_config() -> AdcsControlConfig:
    return AdcsControlConfig()


def default_adcs_control_state() -> AdcsControlState:
    return AdcsControlState()


def default_control_profile_inputs() -> list:
    from .schemas import AdcsControlInput
    return [
        AdcsControlInput(dt_s=1.0, mode="inertialPoint", sigma_bn=(0.2, 0.0, 0.0), omega_bn_b_rad_s=(0.01, 0.0, 0.0)),
        AdcsControlInput(dt_s=1.0, mode="payload", sigma_bn=(0.0, 0.2, 0.0), omega_bn_b_rad_s=(0.0, -0.02, 0.0)),
        AdcsControlInput(dt_s=1.0, mode="safePoint", sigma_bn=(0.0, 0.0, 0.0), omega_bn_b_rad_s=(0.0, 0.01, 0.0), sun_direction_b=(0.0, 1.0, 0.0)),
        AdcsControlInput(dt_s=1.0, mode="detumble", valid_attitude=False, omega_bn_b_rad_s=(0.1, -0.05, 0.02)),
        AdcsControlInput(dt_s=1.0, mode="inertialPoint", valid_attitude=False, valid_rate=True, mode_ready=False),
    ]


def run_control_nominal_case() -> dict[str, object]:
    cfg = build_nominal_adcs_control_config()
    state = default_adcs_control_state()
    inputs = default_control_profile_inputs()
    result = run_control_profile(state, cfg, inputs)
    return summarize_control_profile(result)


def default_adcs_actuator_config() -> AdcsActuatorConfig:
    from components.reaction_wheel import ReactionWheelDynamicsConfig
    from components.mtb import MtbConfig
    from components.cmg import SingleGimbalCmgConfig
    return AdcsActuatorConfig(
        rw=ReactionWheelDynamicsConfig(
            num_wheels=4,
            wheel_inertia_kg_m2=(0.1, 0.1, 0.1, 0.2),
            max_motor_torque_nm=(0.02, 0.02, 0.02, 0.02),
            max_speed_rad_s=(600.0, 600.0, 600.0, 600.0),
            damping_nms=(0.0, 0.0, 0.0, 0.0),
            wheel_axes_B=((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0), (0.577350269, 0.577350269, 0.577350269)),
        ),
        mtb=MtbConfig(num_axes=3, dipole_limit_am2=(0.2, 0.2, 0.2), lag_tau_s=2.0),
        cmg=SingleGimbalCmgConfig(
            wheel_inertia_kg_m2=1.0,
            wheel_speed_rad_s=10.0,
            gimbal_angle_rad=0.0,
            spin_axis_g0_b=(1.0, 0.0, 0.0),
            gimbal_axis_b=(0.0, 0.0, 1.0),
            gimbal_rate_limit_rad_s=1.0,
            wheel_speed_limit_rad_s=100.0,
        ),
    )


def default_adcs_actuator_state() -> AdcsActuatorState:
    from components.reaction_wheel import ReactionWheelState
    from components.mtb import MtbState
    from components.cmg import SingleGimbalCmgState
    return AdcsActuatorState(
        rw=ReactionWheelState((100.0, -80.0, 50.0, -20.0)),
        mtb=MtbState((0.0, 0.0, 0.0)),
        cmg=SingleGimbalCmgState(10.0, 0.0),
    )


def default_actuator_profile_inputs() -> list:
    from .schemas import AdcsActuatorInput
    return [
        AdcsActuatorInput(dt_s=1.0, mode="inertialPoint", requested_torque_b_nm=(0.01, -0.005, 0.0)),
        AdcsActuatorInput(dt_s=1.0, mode="detumble", requested_torque_b_nm=(2.0e-6, 0.0, 0.0), magnetic_field_b_t=(0.0, 0.0, 2.0e-5)),
        AdcsActuatorInput(dt_s=1.0, mode="cmgPoint", requested_torque_b_nm=(0.0, -5.0, 0.0)),
        AdcsActuatorInput(dt_s=1.0, mode="momentumDump", requested_torque_b_nm=(0.0, 0.0, 1.0e-6), magnetic_field_b_t=(0.0, 0.0, 2.0e-5)),
        AdcsActuatorInput(dt_s=1.0, mode="safePoint", requested_torque_b_nm=(0.01, 0.0, 0.0), rw_available=False),
    ]


def build_nominal_adcs_sensor_config() -> AdcsSensorConfig:
    return AdcsSensorConfig()


def build_nominal_adcs_sensor_steps() -> tuple:
    from .schemas import AdcsSensorStepInput
    return (
        AdcsSensorStepInput(
            dt_s=1.0,
            mode="safePoint",
            sigma_bn=(0.01, 0.0, 0.0),
            omega_bn_b_rad_s=(0.001, -0.002, 0.003),
            sun_direction_b=(1.0, 0.0, 0.0),
            shadow_factor=1.0,
            magnetic_field_b_t=(2.0e-5, -1.0e-5, 3.0e-5),
        ),
        AdcsSensorStepInput(
            dt_s=1.0,
            mode="inertialPoint",
            sigma_bn=(0.02, 0.0, 0.0),
            omega_bn_b_rad_s=(0.0005, 0.0, 0.0),
            shadow_factor=1.0,
        ),
        AdcsSensorStepInput(
            dt_s=1.0,
            mode="safePoint",
            sigma_bn=(0.02, 0.0, 0.0),
            omega_bn_b_rad_s=(0.0005, 0.0, 0.0),
            sun_direction_b=(1.0, 0.0, 0.0),
            shadow_factor=0.0,
        ),
        AdcsSensorStepInput(
            dt_s=1.0,
            mode="detumble",
            omega_bn_b_rad_s=(0.01, -0.02, 0.03),
            magnetic_field_b_t=(2.0e-5, 0.0, -4.0e-5),
            shadow_factor=0.0,
        ),
        AdcsSensorStepInput(
            dt_s=1.0,
            mode="inertialPoint",
            sigma_bn=(0.04, 0.0, 0.0),
            omega_bn_b_rad_s=(0.0005, 0.0, 0.0),
            sensor_available={"imu": True, "star_tracker": False, "sun_sensor": True, "magnetometer": True},
        ),
    )


def build_adcs_sensor_subsystem(config: AdcsSensorConfig | None = None, backend: str = "python") -> Any:
    if backend not in {"python", "basilisk"}:
        raise ValueError("ADCS sensor backend must be 'python' or 'basilisk'")
    cfg = config or build_nominal_adcs_sensor_config()
    return {"config": cfg, "state": AdcsSensorState()}


def build_adcs_command_chain_config() -> AdcsCommandChainConfig:
    return AdcsCommandChainConfig(
        sensors=build_nominal_adcs_sensor_config(),
        control=build_nominal_adcs_control_config(),
        actuators=default_adcs_actuator_config(),
    )


def default_adcs_command_chain_state() -> AdcsCommandChainState:
    return AdcsCommandChainState(
        actuators=default_adcs_actuator_state()
    )


def default_command_chain_profile_inputs() -> list[AdcsCommandChainInput]:
    return [
        AdcsCommandChainInput(
            dt_s=1.0,
            mode="inertialPoint",
            sigma_bn=(0.2, 0.0, 0.0),
            omega_bn_b_rad_s=(0.01, 0.0, 0.0),
            shadow_factor=1.0,
        ),
        AdcsCommandChainInput(
            dt_s=1.0,
            mode="safePoint",
            sigma_bn=(0.0, 0.0, 0.0),
            omega_bn_b_rad_s=(0.0, 0.01, 0.0),
            sun_direction_b=(0.0, 1.0, 0.0),
            shadow_factor=1.0,
        ),
        AdcsCommandChainInput(
            dt_s=1.0,
            mode="detumble",
            omega_bn_b_rad_s=(0.1, -0.05, 0.02),
            magnetic_field_b_t=(0.0, 0.0, 2.0e-5),
        ),
        AdcsCommandChainInput(
            dt_s=1.0,
            mode="cmgPoint",
            sigma_bn=(0.05, 0.02, 0.0),
            omega_bn_b_rad_s=(0.0, 0.02, 0.0),
        ),
        AdcsCommandChainInput(
            dt_s=1.0,
            mode="safePoint",
            omega_bn_b_rad_s=(0.0, 0.01, 0.0),
            sun_direction_b=(1.0, 0.0, 0.0),
            shadow_factor=0.0,
        ),
        AdcsCommandChainInput(
            dt_s=1.0,
            mode="inertialPoint",
            sigma_bn=(0.12, 0.0, 0.0),
            omega_bn_b_rad_s=(0.01, 0.0, 0.0),
            rw_available=False,
        ),
    ]


def run_command_chain_nominal_case() -> dict[str, object]:
    cfg = build_adcs_command_chain_config()
    state = default_adcs_command_chain_state()
    inputs = default_command_chain_profile_inputs()
    result = run_adcs_command_chain_profile(state, cfg, inputs)
    return summarize_command_chain_profile(result)


def basilisk_available() -> bool:
    try:
        import Basilisk  # noqa: F401
        return True
    except Exception:
        return False


def run_adcs_actuator_basilisk_smoke() -> dict[str, object]:
    """Smoke-test ADCS actuators through component factories only."""

    if not basilisk_available():
        return {"backend": "basilisk", "available": False, "status": "skipped"}

    rw_bundle = build_reaction_wheel_bundle(model_tag="adcsSmokeRw")
    mtb_bundle = build_mtb_effector_bundle(model_tag="adcsSmokeMtb")
    cmg_bundle = build_vscmg_bundle(model_tag="adcsSmokeVscmg")
    rw_msg = write_rw_torque_message((0.01, -0.01, 0.0))
    vscmg_msg = write_vscmg_array_torque_message([0.0] * int(cmg_bundle.num_cmgs))

    return {
        "backend": "basilisk",
        "available": True,
        "status": "smoke_complete",
        "modules": [
            rw_bundle.effector.__class__.__name__,
            mtb_bundle.effector.__class__.__name__,
            cmg_bundle.effector.__class__.__name__,
        ],
        "messages": {
            "ArrayMotorTorqueMsgPayload": list(rw_msg.read().motorTorque)[:3],
            "MTBCmdMsgPayload": list(mtb_bundle.cmd_msg.read().mtbDipoleCmds)[:3],
            "VSCMGArrayTorqueMsgPayload_available": vscmg_msg is not None,
        },
        "component_factory_only": True,
    }


def run_adcs_actuator_component_factory_smoke() -> dict[str, object]:
    if not basilisk_available():
        return {
            "backend": "basilisk",
            "available": False,
            "status": "Basilisk unavailable; actuator factory bridge skipped",
            "factories": {},
        }

    factories = {}

    try:
        from Basilisk.simulation import spacecraft
        rw_bundle = build_reaction_wheel_bundle(
            "adcsActuatorRw",
            wheel_specs=[
                ReactionWheelSpec(axis_b=(1.0, 0.0, 0.0), model="Honeywell_HR16", max_momentum_nms=50.0),
                ReactionWheelSpec(axis_b=(0.0, 1.0, 0.0), model="Honeywell_HR16", max_momentum_nms=50.0),
                ReactionWheelSpec(axis_b=(0.0, 0.0, 1.0), model="Honeywell_HR16", max_momentum_nms=50.0),
            ],
        )
        sc = spacecraft.Spacecraft()
        sc.ModelTag = "testSpacecraft"
        sc.hub.mHub = 100.0
        sc.hub.r_BcB_B = [[0.0], [0.0], [0.0]]
        sc.hub.IHubPntBc_B = [[10.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 10.0]]
        attach_reaction_wheels_to_spacecraft(rw_bundle, sc)
        factories["reaction_wheel"] = {"status": "ok", "wheel_count": rw_bundle.wheel_count}
    except Exception as exc:
        factories["reaction_wheel"] = {"status": "failed", "reason": str(exc)}

    try:
        mtb_bundle = build_mtb_effector_bundle(
            "adcsActuatorMtb",
            bar_count=3,
            max_dipole_am2=0.2,
        )
        sc_mtb = spacecraft.Spacecraft()
        sc_mtb.ModelTag = "testSpacecraftMtb"
        sc_mtb.hub.mHub = 100.0
        sc_mtb.hub.r_BcB_B = [[0.0], [0.0], [0.0]]
        sc_mtb.hub.IHubPntBc_B = [[10.0, 0.0, 0.0], [0.0, 10.0, 0.0], [0.0, 0.0, 10.0]]
        attach_mtb_to_spacecraft(mtb_bundle, sc_mtb)
        factories["mtb"] = {"status": "ok", "bar_count": mtb_bundle.bar_count}
    except Exception as exc:
        factories["mtb"] = {"status": "failed", "reason": str(exc)}

    all_ok = all(f["status"] == "ok" for f in factories.values())
    return {
        "backend": "basilisk",
        "available": True,
        "status": "factory_smoke_complete" if all_ok else "partial_factory_smoke",
        "factories": factories,
        "all_factories_ok": all_ok,
    }


def build_nominal_momentum_dump_config() -> MomentumDumpConfig:
    return MomentumDumpConfig()


def run_momentum_dump_case(config: MomentumDumpConfig | None = None) -> MomentumDumpResult:
    cfg = config or build_nominal_momentum_dump_config()
    return run_momentum_dump(cfg)


def build_nominal_magnetic_detumble_config() -> MagneticDetumbleConfig:
    return MagneticDetumbleConfig()


def run_magnetic_detumble_case(config: MagneticDetumbleConfig | None = None) -> MagneticDetumbleResult:
    cfg = config or build_nominal_magnetic_detumble_config()
    return run_magnetic_detumble(cfg)


def build_adcs_sensor_configs(
    imu_degradation=None,
    star_tracker_degradation=None,
    sun_sensor_degradation=None,
    magnetometer_degradation=None,
):
    return {
        "imu": build_nominal_imu_config(degradation=imu_degradation),
        "star_tracker": build_nominal_star_tracker_config(degradation=star_tracker_degradation),
        "sun_sensor": build_nominal_sun_sensor_config(degradation=sun_sensor_degradation),
        "magnetometer": build_nominal_magnetometer_config(degradation=magnetometer_degradation),
    }


def build_adcs_basilisk_sensors(
    model_tag_prefix: str = "adcsSensor",
    sc_state_msg=None,
    sun_in_msg=None,
    eclipse_in_msg=None,
    mag_field_msg=None,
):
    if not basilisk_available():
        return {"available": False, "status": "Basilisk unavailable"}
    
    sensors = {}
    try:
        sensors["imu"] = build_imu_sensor(
            model_tag=f"{model_tag_prefix}Imu",
            sc_state_msg=sc_state_msg,
        )
    except Exception as exc:
        sensors["imu"] = {"status": "failed", "reason": str(exc)}
    
    try:
        sensors["star_tracker"] = build_star_tracker(
            model_tag=f"{model_tag_prefix}StarTracker",
            sc_state_msg=sc_state_msg,
        )
    except Exception as exc:
        sensors["star_tracker"] = {"status": "failed", "reason": str(exc)}
    
    try:
        sensors["sun_sensor"] = build_coarse_sun_sensor(
            model_tag=f"{model_tag_prefix}CoarseSunSensor",
            sc_state_msg=sc_state_msg,
            sun_in_msg=sun_in_msg,
            eclipse_in_msg=eclipse_in_msg,
        )
    except Exception as exc:
        sensors["sun_sensor"] = {"status": "failed", "reason": str(exc)}
    
    try:
        sensors["magnetometer"] = build_magnetometer(
            model_tag=f"{model_tag_prefix}Magnetometer",
            sc_state_msg=sc_state_msg,
            mag_field_msg=mag_field_msg,
        )
    except Exception as exc:
        sensors["magnetometer"] = {"status": "failed", "reason": str(exc)}
    
    return {"available": True, "status": "sensor_build_complete", "sensors": sensors}


def build_adcs_cmg_configs(cmg_degradation=None):
    return {
        "cmg": build_nominal_cmg_config(),
    }


def build_adcs_vscmg_bundle(model_tag: str = "adcsVscmg", cmg_specs=None):
    if not basilisk_available():
        return {"available": False, "status": "Basilisk unavailable"}
    
    try:
        bundle = build_vscmg_bundle(model_tag=model_tag, cmg_specs=cmg_specs)
        return {"available": True, "status": "vscmg_build_complete", "bundle": bundle}
    except Exception as exc:
        return {"available": True, "status": "vscmg_build_failed", "reason": str(exc)}


def attach_adcs_vscmg_to_spacecraft(bundle_result, spacecraft):
    if not bundle_result.get("available") or "bundle" not in bundle_result:
        return {"status": "failed", "reason": "VSCMG bundle not available"}
    
    attach_vscmg_to_spacecraft(bundle_result["bundle"], spacecraft)
    return {"status": "ok", "num_cmgs": bundle_result["bundle"].num_cmgs}


def build_adcs_actuator_configs(rw_degradation=None, mtb_degradation=None, cmg_degradation=None):
    return {
        "reaction_wheel": default_adcs_actuator_config().rw,
        "mtb": default_adcs_actuator_config().mtb,
        "cmg": default_adcs_actuator_config().cmg,
    }


def build_adcs_components(
    rw_specs=None,
    mtb_specs=None,
    cmg_specs=None,
    model_tag_prefix: str = "adcs",
):
    return {
        "reaction_wheel_bundle": build_reaction_wheel_bundle(
            model_tag=f"{model_tag_prefix}ReactionWheelCluster",
            wheel_specs=rw_specs,
        ),
        "mtb_bundle": build_mtb_effector_bundle(
            model_tag=f"{model_tag_prefix}MtbEffector",
            specs=mtb_specs,
        ),
        "vscmg_bundle": build_vscmg_bundle(
            model_tag=f"{model_tag_prefix}Vscmg",
            cmg_specs=cmg_specs,
        ) if basilisk_available() else None,
    }

@dataclass(frozen=True)
class ADCSBasiliskSimContext:
    """Built ADCS Basilisk simulation graph; execution is left to runner.py."""

    subsystem: str
    config: "ADCSBasiliskConfig"
    simulation: Any
    process: Any
    task: Any
    task_name: str
    modules: dict[str, Any]
    recorders: dict[str, Any]
    message_handles: dict[str, Any]
    base_parameters: dict[str, Any]
    component_sources: dict[str, str]


def _require_adcs_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import spacecraft  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
        from components.reaction_wheel.builder import require_basilisk_reaction_wheel
        from components.mtb.builder import require_basilisk_mtb
        require_basilisk_reaction_wheel()
        require_basilisk_mtb()
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk ADCS modules are unavailable: {exc}") from exc


def build_adcs_basilisk_sim(config: "ADCSBasiliskConfig | None" = None) -> ADCSBasiliskSimContext:
    """Build the ADCS Basilisk simulation context without executing it.

    Spacecraft/process/task creation lives here, but all component-level ADCS
    objects are created through ``components/*/builder.py`` factories.
    """

    from Basilisk.architecture import messaging
    from Basilisk.simulation import spacecraft
    from Basilisk.utilities import SimulationBaseClass, macros

    _require_adcs_basilisk()
    cfg = config or ADCSBasiliskConfig()
    if cfg.step_s <= 0 or cfg.duration_s <= 0 or cfg.sample_s <= 0:
        raise ValueError("duration_s, step_s and sample_s must be positive")

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("adcsBasiliskProcess")
    task_name = "adcsBasiliskTask"
    task = sim.CreateNewTask(task_name, macros.sec2nano(float(cfg.step_s)))
    process.addTask(task)

    sc = spacecraft.Spacecraft()
    sc.ModelTag = "adcsSpacecraft"
    sc.hub.mHub = float(cfg.spacecraft_mass_kg)
    inertia = list(float(x) for x in cfg.spacecraft_inertia_kg_m2)
    sc.hub.IHubPntBc_B = [inertia[0:3], inertia[3:6], inertia[6:9]]
    sc.hub.r_CN_NInit = [[0.0], [0.0], [0.0]]
    sc.hub.v_CN_NInit = [[0.0], [0.0], [0.0]]
    sc.hub.sigma_BNInit = [[0.0], [0.0], [0.0]]
    sc.hub.omega_BN_BInit = [[0.0], [0.0], [0.0]]

    rw_bundle = build_reaction_wheel_bundle(model_tag="adcsReactionWheelCluster")
    rw_cmd_msg = write_rw_torque_message(cfg.rw_motor_torques_nm)
    rw_bundle.effector.rwMotorCmdInMsg.subscribeTo(rw_cmd_msg)
    attach_reaction_wheels_to_spacecraft(rw_bundle, sc)

    mtb_bundle = build_mtb_effector_bundle(
        model_tag="adcsMtbEffector",
        cmd_dipoles_am2=cfg.mtb_dipoles_am2,
        magnetic_field_t=cfg.magnetic_field_t,
    )
    attach_mtb_to_spacecraft(mtb_bundle, sc)

    modules: dict[str, Any] = {
        "spacecraft": sc,
        "reaction_wheel_bundle": rw_bundle,
        "reaction_wheel_effector": rw_bundle.effector,
        "mtb_bundle": mtb_bundle,
        "mtb_effector": mtb_bundle.effector,
    }
    message_handles: dict[str, Any] = {
        "spacecraft_state": sc.scStateOutMsg,
        "rw_command": rw_cmd_msg,
        "rw_config": rw_bundle.config_msg,
        "mtb_command": mtb_bundle.cmd_msg,
        "mtb_config": mtb_bundle.config_msg,
        "magnetic_field": mtb_bundle.field_msg,
    }

    if cfg.include_cmg:
        vscmg_bundle = build_vscmg_bundle(model_tag="adcsVscmgEffector")
        attach_vscmg_to_spacecraft(vscmg_bundle, sc)
        vscmg_cmd_msg = write_vscmg_array_torque_message([0.0] * int(vscmg_bundle.num_cmgs))
        vscmg_bundle.effector.cmdsInMsg.subscribeTo(vscmg_cmd_msg)
        modules["vscmg_bundle"] = vscmg_bundle
        modules["vscmg_effector"] = vscmg_bundle.effector
        message_handles["vscmg_command"] = vscmg_cmd_msg

    sim.AddModelToTask(task_name, sc)
    sim.AddModelToTask(task_name, rw_bundle.effector)
    sim.AddModelToTask(task_name, mtb_bundle.effector)
    if "vscmg_effector" in modules:
        sim.AddModelToTask(task_name, modules["vscmg_effector"])

    sensor_modules: dict[str, Any] = {}
    if cfg.include_sensors:
        sun_payload = messaging.SpicePlanetStateMsgPayload()
        sun_payload.PositionVector = [1.5e11, 0.0, 0.0]
        sun_msg = messaging.SpicePlanetStateMsg().write(sun_payload)
        eclipse_payload = messaging.EclipseMsgPayload()
        if hasattr(eclipse_payload, "illuminationFactor"):
            eclipse_payload.illuminationFactor = 1.0
        eclipse_msg = messaging.EclipseMsg().write(eclipse_payload)
        mag_payload = messaging.MagneticFieldMsgPayload()
        mag_payload.magField_N = list(cfg.magnetic_field_t)
        mag_msg = messaging.MagneticFieldMsg().write(mag_payload)
        sensor_modules = {
            "imu": build_imu_sensor("adcsImu", sc_state_msg=sc.scStateOutMsg),
            "star_tracker": build_star_tracker("adcsStarTracker", sc_state_msg=sc.scStateOutMsg),
            "sun_sensor": build_coarse_sun_sensor("adcsSunSensor", sc_state_msg=sc.scStateOutMsg, sun_in_msg=sun_msg, eclipse_in_msg=eclipse_msg),
            "magnetometer": build_magnetometer("adcsMagnetometer", sc_state_msg=sc.scStateOutMsg, mag_field_msg=mag_msg),
        }
        modules.update(sensor_modules)
        message_handles.update({"sun_state": sun_msg, "eclipse": eclipse_msg, "sensor_magnetic_field": mag_msg})
        for sensor in sensor_modules.values():
            sim.AddModelToTask(task_name, sensor)

    period = macros.sec2nano(float(cfg.sample_s))
    sc_rec = sc.scStateOutMsg.recorder(period)
    recorders: dict[str, Any] = {"spacecraft": sc_rec}
    sim.AddModelToTask(task_name, sc_rec)
    try:
        rw_speed_rec = rw_bundle.effector.rwSpeedOutMsg.recorder(period)
        recorders["reaction_wheel_speed"] = rw_speed_rec
        sim.AddModelToTask(task_name, rw_speed_rec)
    except Exception as exc:
        # Recorder availability is diagnostic rather than a required dynamics
        # connection, but the omission must remain visible to validation.
        message_handles["reaction_wheel_speed_recorder_error"] = f"{type(exc).__name__}: {exc}"

    return ADCSBasiliskSimContext(
        subsystem="adcs",
        config=cfg,
        simulation=sim,
        process=process,
        task=task,
        task_name=task_name,
        modules=modules,
        recorders=recorders,
        message_handles=message_handles,
        base_parameters={
            "period_ns": period,
            "spacecraft_mass_kg": float(cfg.spacecraft_mass_kg),
            "spacecraft_inertia_kg_m2": tuple(float(x) for x in cfg.spacecraft_inertia_kg_m2),
            "rw_motor_torques_nm": tuple(float(x) for x in cfg.rw_motor_torques_nm),
            "mtb_dipoles_am2": tuple(float(x) for x in cfg.mtb_dipoles_am2),
            "magnetic_field_t": tuple(float(x) for x in cfg.magnetic_field_t),
        },
        component_sources={
            "reaction_wheel": "components.reaction_wheel.builder.build_reaction_wheel_bundle",
            "mtb": "components.mtb.builder.build_mtb_effector_bundle",
            "cmg": "components.cmg.builder.build_vscmg_bundle",
            "imu": "components.imu.builder.build_imu_sensor",
            "star_tracker": "components.star_tracker.builder.build_star_tracker",
            "sun_sensor": "components.sun_sensor.builder.build_coarse_sun_sensor",
            "magnetometer": "components.magnetometer.builder.build_magnetometer",
        },
    )

