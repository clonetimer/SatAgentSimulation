"""Basilisk-native ADCS sensor fusion focused runners.

This module is the v8.19-G/H/I progression after the ST/IMU sensor-in-the-loop
feasibility runner.  It keeps the same real Basilisk dynamics/control graph but
replaces the direct ST/IMU bridge with a Basilisk Python ``SysModel`` estimator
that is scheduled inside ``SimBaseClass`` and writes ``NavAttMsg``.

Implemented levels:
- v8.19-G: ideal sensor telemetry fusion baseline
- v8.19-H: noisy sensor and dropout stress
- v8.19-I: MEKF-style prototype baseline

Boundary: the MEKF-style prototype is an engineering navigation-estimator
prototype for closed-loop feasibility.  It is not claimed to be a flight-grade
EKF/MEKF implementation or externally validated navigation filter.
"""
from __future__ import annotations

import csv
import json
from dataclasses import asdict, dataclass
from math import sqrt, sin, cos, isfinite
from pathlib import Path
from typing import Any, Literal

import numpy as np

from Basilisk.architecture import messaging, sysModel
from Basilisk.utilities import RigidBodyKinematics as rbk

from .schemas import RwOnlyClosedLoopConfig


def _is_finite_scalar(value: Any) -> bool:
    try:
        return isfinite(float(value))
    except Exception:
        return False


def _is_finite_vector(vec: Any, *, expected_len: int | None = None) -> bool:
    try:
        values = [float(x) for x in vec]
    except Exception:
        return False
    if expected_len is not None and len(values) != expected_len:
        return False
    return all(isfinite(x) for x in values)


def _norm(vec: Any) -> float:
    values = [float(x) for x in vec]
    if not all(isfinite(x) for x in values):
        return float("nan")
    return float(sqrt(sum(x * x for x in values)))


def _safe_vector(vec: Any, fallback: Any, *, expected_len: int) -> tuple[list[float], bool]:
    try:
        values = [float(x) for x in vec]
    except Exception:
        values = []
    if len(values) == expected_len and all(isfinite(x) for x in values):
        return values, True
    fb = [float(x) for x in fallback]
    if len(fb) != expected_len or not all(isfinite(x) for x in fb):
        fb = [0.0] * expected_len
    return fb, False


def _safe_ep_to_mrp(q: Any, fallback_sigma: Any) -> tuple[list[float], bool]:
    try:
        quat = [float(x) for x in q]
    except Exception:
        quat = []
    q_norm = sqrt(sum(x * x for x in quat)) if len(quat) == 4 and all(isfinite(x) for x in quat) else 0.0
    if q_norm > 1.0e-12:
        quat = [x / q_norm for x in quat]
        try:
            sigma = [float(x) for x in rbk.EP2MRP(quat)]
        except Exception:
            sigma = []
        if len(sigma) == 3 and all(isfinite(x) for x in sigma):
            return sigma, True
    fallback, _ok = _safe_vector(fallback_sigma, (0.0, 0.0, 0.0), expected_len=3)
    return fallback, False


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


FusionMode = Literal["ideal", "noisy_dropout", "mekf_prototype"]


@dataclass(frozen=True)
class SensorFusionConfig:
    """Configuration for sensor fusion focused ADCS runner."""

    duration_s: float = 300.0
    dyn_step_s: float = 0.2
    fsw_step_s: float = 0.2
    sample_s: float = 2.0
    rw_config: RwOnlyClosedLoopConfig = RwOnlyClosedLoopConfig(duration_s=300.0, dyn_step_s=0.2, fsw_step_s=0.2, sample_s=2.0)
    fusion_mode: FusionMode = "ideal"
    sun_position_n_m: tuple[float, float, float] = (1.496e11, 0.0, 0.0)
    magnetic_field_n_t: tuple[float, float, float] = (2.0e-5, -1.0e-5, 3.0e-5)
    max_final_attitude_ratio: float = 0.35
    max_final_attitude_error_norm: float = 0.14
    attitude_noise_mrp: tuple[float, float, float] = (0.0, 0.0, 0.0)
    imu_rate_bias_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    imu_rate_bias_drift_rad_s2: tuple[float, float, float] = (0.0, 0.0, 0.0)
    imu_rate_random_walk_rad_s: tuple[float, float, float] = (0.0, 0.0, 0.0)
    st_dropout_start_s: float = -1.0
    st_dropout_end_s: float = -1.0
    alpha_st: float = 1.0
    mekf_gain: float = 0.35
    mekf_bias_gain: float = 0.02


@dataclass(frozen=True)
class SensorFusionSummary:
    backend: str
    subsystem: str
    basilisk_simbase_used: bool
    execute_simulation_used: bool
    sensor_fusion_in_loop: bool
    estimator_type: str
    native_modules: tuple[str, ...]
    custom_modules: tuple[str, ...]
    sample_count: int
    initial_attitude_error_norm: float
    final_attitude_error_norm: float
    peak_attitude_error_norm: float
    final_attitude_error_ratio: float
    peak_attitude_error_ratio: float
    initial_rate_error_norm_rad_s: float
    final_rate_error_norm_rad_s: float
    final_rate_error_ratio: float
    estimator_quality_min: float
    st_dropout_sample_count: int
    max_nav_innovation_norm: float
    status: str
    not_claimed: tuple[str, ...]


class StarTrackerImuFusionEstimator(sysModel.SysModel):
    """Scheduled sensor-fusion estimator writing ``NavAttMsg``.

    The estimator reads Basilisk StarTracker and IMU outputs.  It supports an
    ideal direct fusion baseline, deterministic noise/dropout stress, and a
    MEKF-style complementary update.  CSS/TAM are recorded by the runner as
    consistency side telemetry, but are not yet used as attitude sources.
    """

    def __init__(self, config: SensorFusionConfig, model_tag: str = "starTrackerImuFusionEstimator"):
        super().__init__()
        self.ModelTag = model_tag
        self.config = config
        self.starTrackerInMsg = messaging.STSensorMsgReader()
        self.imuInMsg = messaging.IMUSensorMsgReader()
        self.scStateInMsg = messaging.SCStatesMsgReader()
        self.attOutMsg = messaging.NavAttMsg()
        self.trace: list[dict[str, float]] = []
        self._initialized = False
        self._sigma_est = [0.0, 0.0, 0.0]
        self._omega_est = [0.0, 0.0, 0.0]
        self._bias_est = [0.0, 0.0, 0.0]
        self._last_t = 0.0

    def Reset(self, CurrentSimNanos: int) -> None:
        self.trace.clear()
        self._initialized = False
        self._sigma_est = [0.0, 0.0, 0.0]
        self._omega_est = [0.0, 0.0, 0.0]
        self._bias_est = [0.0, 0.0, 0.0]
        self._last_t = 0.0
        self.UpdateState(CurrentSimNanos)

    def _is_dropout(self, t: float) -> bool:
        cfg = self.config
        return cfg.st_dropout_start_s <= t <= cfg.st_dropout_end_s and cfg.st_dropout_start_s >= 0.0

    def _noise(self, t: float) -> list[float]:
        ax, ay, az = self.config.attitude_noise_mrp
        return [float(ax) * sin(0.07 * t), float(ay) * cos(0.05 * t), float(az) * sin(0.11 * t + 0.3)]

    def _gyro_random_walk_proxy(self, t: float) -> list[float]:
        wx, wy, wz = self.config.imu_rate_random_walk_rad_s
        return [
            float(wx) * sin(0.019 * t + 0.11),
            float(wy) * sin(0.023 * t + 0.37),
            float(wz) * sin(0.017 * t + 0.73),
        ]

    def UpdateState(self, CurrentSimNanos: int) -> None:
        from Basilisk.utilities import macros

        t = float(CurrentSimNanos) * macros.NANO2SEC
        dt = max(t - self._last_t, 0.0)
        self._last_t = t
        st = self.starTrackerInMsg()
        imu = self.imuInMsg()
        sc_state = self.scStateInMsg() if self.scStateInMsg.isLinked() else None

        fallback_sigma, sc_sigma_valid = _safe_vector(
            getattr(sc_state, "sigma_BN", ()) if sc_state is not None else (),
            self._sigma_est if self._initialized else self.config.rw_config.initial_sigma_bn,
            expected_len=3,
        )
        if not sc_sigma_valid and self._initialized:
            fallback_sigma = list(self._sigma_est)
        sigma_meas, st_valid = _safe_ep_to_mrp(getattr(st, "qInrtl2Case", ()), fallback_sigma)

        n = self._noise(t)
        sigma_meas_noisy = [sigma_meas[i] + n[i] for i in range(3)]
        if not all(isfinite(x) for x in sigma_meas_noisy):
            sigma_meas_noisy = list(fallback_sigma)
            st_valid = False

        base_bias = [float(x) for x in self.config.imu_rate_bias_rad_s]
        drift = [float(x) for x in self.config.imu_rate_bias_drift_rad_s2]
        random_walk = self._gyro_random_walk_proxy(t)
        rate_bias = [base_bias[i] + drift[i] * t + random_walk[i] for i in range(3)]

        fallback_omega, sc_omega_valid = _safe_vector(
            getattr(sc_state, "omega_BN_B", ()) if sc_state is not None else (),
            self._omega_est if self._initialized else self.config.rw_config.initial_omega_bn_b_rad_s,
            expected_len=3,
        )
        if not sc_omega_valid and self._initialized:
            fallback_omega = list(self._omega_est)
        imu_omega, imu_valid = _safe_vector(getattr(imu, "AngVelPlatform", ()), fallback_omega, expected_len=3)
        omega_meas = [imu_omega[i] + rate_bias[i] for i in range(3)]
        if not all(isfinite(x) for x in omega_meas):
            omega_meas = list(fallback_omega)
            imu_valid = False

        dropout = self._is_dropout(t)

        if not self._initialized:
            self._sigma_est = list(sigma_meas_noisy)
            self._omega_est = list(omega_meas)
            self._initialized = True
        else:
            mode = self.config.fusion_mode
            if mode == "ideal":
                self._sigma_est = list(sigma_meas)
                self._omega_est = list(omega_meas)
            elif mode == "noisy_dropout":
                alpha = float(self.config.alpha_st)
                if dropout:
                    self._sigma_est = [self._sigma_est[i] + 0.25 * self._omega_est[i] * dt for i in range(3)]
                else:
                    self._sigma_est = [(1.0 - alpha) * self._sigma_est[i] + alpha * sigma_meas_noisy[i] for i in range(3)]
                self._omega_est = list(omega_meas)
            else:
                gain = float(self.config.mekf_gain)
                if not dropout:
                    innov = [sigma_meas_noisy[i] - self._sigma_est[i] for i in range(3)]
                    self._sigma_est = [self._sigma_est[i] + gain * innov[i] for i in range(3)]
                    for i in range(3):
                        self._bias_est[i] += float(self.config.mekf_bias_gain) * (omega_meas[i] - self._omega_est[i])
                else:
                    self._sigma_est = [self._sigma_est[i] + 0.25 * self._omega_est[i] * dt for i in range(3)]
                self._omega_est = [omega_meas[i] - self._bias_est[i] for i in range(3)]

        if not _is_finite_vector(self._sigma_est, expected_len=3):
            self._sigma_est = list(fallback_sigma)
        if not _is_finite_vector(self._omega_est, expected_len=3):
            self._omega_est = list(fallback_omega)

        innovation = [sigma_meas_noisy[i] - self._sigma_est[i] for i in range(3)]
        innovation_norm = _norm(innovation)
        if not isfinite(innovation_norm):
            innovation_norm = 0.0
        quality = max(0.0, 1.0 - min(innovation_norm / 0.25, 1.0))
        if not st_valid:
            quality = min(quality, 0.65)
        if not imu_valid:
            quality = min(quality, 0.65)
        if dropout:
            quality = min(quality, 0.45)

        payload = messaging.NavAttMsgPayload()
        payload.sigma_BN = [float(x) for x in self._sigma_est]
        payload.omega_BN_B = [float(x) for x in self._omega_est]
        payload.vehSunPntBdy = [0.0, 0.0, 0.0]
        payload.timeTag = t
        self.attOutMsg.write(payload, CurrentSimNanos, self.moduleID)
        self.trace.append({
            "time_s": t,
            "fusion_mode": {"ideal": 0.0, "noisy_dropout": 1.0, "mekf_prototype": 2.0}[self.config.fusion_mode],
            "st_dropout": 1.0 if dropout else 0.0,
            "nav_sigma_norm": _norm(self._sigma_est),
            "nav_omega_norm_rad_s": _norm(self._omega_est),
            "nav_innovation_norm": innovation_norm,
            "estimator_quality": quality,
            "bias_est_norm_rad_s": _norm(self._bias_est),
            "imu_injected_bias_norm_rad_s": _norm(rate_bias),
            "imu_bias_drift_norm_rad_s2": _norm(drift),
            "imu_random_walk_norm_rad_s": _norm(random_walk),
            "star_tracker_valid": 1.0 if st_valid else 0.0,
            "imu_valid": 1.0 if imu_valid else 0.0,
            "spacecraft_state_sigma_valid": 1.0 if sc_sigma_valid else 0.0,
            "spacecraft_state_rate_valid": 1.0 if sc_omega_valid else 0.0,
        })


def _require_basilisk() -> None:
    try:
        from Basilisk.utilities import SimulationBaseClass, macros  # noqa: F401
        from Basilisk.simulation import spacecraft, imuSensor, starTracker, coarseSunSensor, magnetometer  # noqa: F401
        from Basilisk.fswAlgorithms import inertial3D, attTrackingError, mrpFeedback, rwMotorTorque  # noqa: F401
        from Basilisk.architecture import messaging  # noqa: F401
    except Exception as exc:  # pragma: no cover
        raise RuntimeError(f"Basilisk ADCS sensor fusion modules are unavailable: {exc}") from exc


def _run_sensor_fusion(config: SensorFusionConfig) -> tuple[SensorFusionSummary, tuple[dict[str, Any], ...]]:
    _require_basilisk()
    cfg = config
    rw_cfg = cfg.rw_config
    if min(cfg.duration_s, cfg.dyn_step_s, cfg.fsw_step_s, cfg.sample_s) <= 0:
        raise ValueError("sensor fusion timing parameters must be positive")

    from Basilisk.architecture import messaging
    from Basilisk.fswAlgorithms import attTrackingError, inertial3D, mrpFeedback, rwMotorTorque
    from Basilisk.simulation import spacecraft, imuSensor, starTracker, coarseSunSensor, magnetometer
    from Basilisk.utilities import SimulationBaseClass, macros
    from components.reaction_wheel.builder import build_reaction_wheel_bundle
    from components.reaction_wheel.schemas import ReactionWheelSpec

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("adcsSensorFusionProcess")
    sim_task_name = "simTask"
    fsw_task_name = "fswTask"
    process.addTask(sim.CreateNewTask(sim_task_name, macros.sec2nano(float(cfg.dyn_step_s))), 10)
    process.addTask(sim.CreateNewTask(fsw_task_name, macros.sec2nano(float(cfg.fsw_step_s))), 15)

    sc_object = spacecraft.Spacecraft(); sc_object.ModelTag = "sensorFusionSpacecraft"
    sc_object.hub.mHub = float(rw_cfg.spacecraft_mass_kg)
    sc_object.hub.r_BcB_B = [[0.0], [0.0], [0.0]]
    sc_object.hub.IHubPntBc_B = _matrix_3x3(rw_cfg.spacecraft_inertia_kg_m2)
    sc_object.hub.sigma_BNInit = _matrix_column(rw_cfg.initial_sigma_bn)
    sc_object.hub.omega_BN_BInit = _matrix_column(rw_cfg.initial_omega_bn_b_rad_s)
    sim.AddModelToTask(sim_task_name, sc_object)

    rw_bundle = build_reaction_wheel_bundle(
        model_tag="RW_cluster_sensor_fusion",
        wheel_specs=[
            ReactionWheelSpec(
                axis_b=tuple(w.axis_b),
                model=str(w.model),
                max_momentum_nms=float(w.max_momentum_nms),
                initial_speed_rpm=float(w.initial_speed_rpm),
                wheel_js=float(w.wheel_js),
                u_max_nm=float(w.u_max_nm),
                omega_max_rad_s=float(w.omega_max_rad_s),
                use_rw_friction=bool(w.use_rw_friction),
                fCoulomb=float(w.fCoulomb),
                fStatic=float(w.fStatic),
                betaStatic=float(w.betaStatic),
                cViscous=float(w.cViscous),
            )
            for w in rw_cfg.wheels
        ],
    )
    rw_state_effector = rw_bundle.effector
    rw_bundle.factory.addToSpacecraft("RW_cluster_sensor_fusion", rw_state_effector, sc_object)
    sim.AddModelToTask(sim_task_name, rw_state_effector)
    rw_config_msg = rw_bundle.config_msg

    imu = imuSensor.ImuSensor(); imu.ModelTag = "fusionImu"; imu.scStateInMsg.subscribeTo(sc_object.scStateOutMsg); sim.AddModelToTask(sim_task_name, imu)
    st = starTracker.StarTracker(); st.ModelTag = "fusionStarTracker"; st.scStateInMsg.subscribeTo(sc_object.scStateOutMsg); sim.AddModelToTask(sim_task_name, st)

    sun_payload = messaging.SpicePlanetStateMsgPayload(); sun_payload.PositionVector = [float(x) for x in cfg.sun_position_n_m]
    sun_msg = messaging.SpicePlanetStateMsg().write(sun_payload)
    eclipse_payload = messaging.EclipseMsgPayload(); eclipse_payload.illuminationFactor = 1.0
    eclipse_msg = messaging.EclipseMsg().write(eclipse_payload)
    css = coarseSunSensor.CoarseSunSensor(); css.ModelTag = "fusionCss"; css.nHat_B = [1.0,0.0,0.0]; css.fov = 3.141592653589793; css.scaleFactor=1.0
    css.stateInMsg.subscribeTo(sc_object.scStateOutMsg); css.sunInMsg.subscribeTo(sun_msg); css.sunEclipseInMsg.subscribeTo(eclipse_msg); sim.AddModelToTask(sim_task_name, css)

    mag_payload = messaging.MagneticFieldMsgPayload(); mag_payload.magField_N = [float(x) for x in cfg.magnetic_field_n_t]
    mag_msg = messaging.MagneticFieldMsg().write(mag_payload)
    tam = magnetometer.Magnetometer(); tam.ModelTag = "fusionMagnetometer"; tam.stateInMsg.subscribeTo(sc_object.scStateOutMsg); tam.magInMsg.subscribeTo(mag_msg); sim.AddModelToTask(sim_task_name, tam)

    estimator = StarTrackerImuFusionEstimator(cfg)
    estimator.starTrackerInMsg.subscribeTo(st.sensorOutMsg); estimator.imuInMsg.subscribeTo(imu.sensorOutMsg); estimator.scStateInMsg.subscribeTo(sc_object.scStateOutMsg)
    sim.AddModelToTask(fsw_task_name, estimator)

    inertial_guidance = inertial3D.inertial3D(); inertial_guidance.ModelTag = "fusionInertial3D"; inertial_guidance.sigma_R0N = list(rw_cfg.target_sigma_rn); sim.AddModelToTask(fsw_task_name, inertial_guidance)
    attitude_error = attTrackingError.attTrackingError(); attitude_error.ModelTag = "fusionAttTrackingError"
    attitude_error.attRefInMsg.subscribeTo(inertial_guidance.attRefOutMsg); attitude_error.attNavInMsg.subscribeTo(estimator.attOutMsg); sim.AddModelToTask(fsw_task_name, attitude_error)

    vehicle_config = messaging.VehicleConfigMsgPayload(ISCPntB_B=list(rw_cfg.spacecraft_inertia_kg_m2))
    vehicle_config_msg = messaging.VehicleConfigMsg().write(vehicle_config)
    controller = mrpFeedback.mrpFeedback(); controller.ModelTag = "fusionMrpFeedback"
    controller.K = float(rw_cfg.control_k); controller.P = float(rw_cfg.control_p); controller.Ki = float(rw_cfg.control_ki); controller.integralLimit = float(rw_cfg.integral_limit)
    rw_feedback_payload = messaging.RWSpeedMsgPayload()
    rw_feedback_speeds = list(rw_feedback_payload.wheelSpeeds)
    for idx, wheel in enumerate(rw_cfg.wheels):
        rw_feedback_speeds[idx] = float(wheel.initial_speed_rpm) * 2.0 * 3.141592653589793 / 60.0
    rw_feedback_payload.wheelSpeeds = rw_feedback_speeds
    rw_feedback_speed_msg = messaging.RWSpeedMsg().write(rw_feedback_payload)
    controller.guidInMsg.subscribeTo(attitude_error.attGuidOutMsg); controller.vehConfigInMsg.subscribeTo(vehicle_config_msg); controller.rwParamsInMsg.subscribeTo(rw_config_msg); controller.rwSpeedsInMsg.subscribeTo(rw_feedback_speed_msg); sim.AddModelToTask(fsw_task_name, controller)
    rw_torque_allocator = rwMotorTorque.rwMotorTorque(); rw_torque_allocator.ModelTag = "fusionRwMotorTorque"; rw_torque_allocator.controlAxes_B = [1.0,0.0,0.0,0.0,1.0,0.0,0.0,0.0,1.0]
    rw_torque_allocator.rwParamsInMsg.subscribeTo(rw_config_msg); rw_torque_allocator.vehControlInMsg.subscribeTo(controller.cmdTorqueOutMsg); sim.AddModelToTask(fsw_task_name, rw_torque_allocator)
    rw_state_effector.rwMotorCmdInMsg.subscribeTo(rw_torque_allocator.rwMotorTorqueOutMsg)

    sample_time = macros.sec2nano(float(cfg.sample_s))
    state_log = sc_object.scStateOutMsg.recorder(sample_time)
    attitude_log = attitude_error.attGuidOutMsg.recorder(sample_time)
    rw_speed_log = rw_state_effector.rwSpeedOutMsg.recorder(sample_time)
    imu_log = imu.sensorOutMsg.recorder(sample_time)
    st_log = st.sensorOutMsg.recorder(sample_time)
    css_log = css.cssDataOutMsg.recorder(sample_time)
    tam_log = tam.tamDataOutMsg.recorder(sample_time)
    for logger in (state_log, rw_speed_log, imu_log, st_log, css_log, tam_log):
        sim.AddModelToTask(sim_task_name, logger)
    sim.AddModelToTask(fsw_task_name, attitude_log)

    sim.InitializeSimulation(); sim.ConfigureStopTime(macros.sec2nano(float(cfg.duration_s))); sim.ExecuteSimulation()

    n_wheels = len(rw_cfg.wheels)
    time_s = [float(t) * macros.NANO2SEC for t in attitude_log.times()]
    sigma_br = np.array(attitude_log.sigma_BR, dtype=float).tolist()
    omega_br_b = np.array(attitude_log.omega_BR_B, dtype=float).tolist()
    rw_speeds = np.array(rw_speed_log.wheelSpeeds, dtype=float)[:, :n_wheels].tolist()
    attitude_error_norm = [_norm(row) for row in sigma_br]
    rate_error_norm = [_norm(row) for row in omega_br_b]
    est_trace = {round(row["time_s"], 9): row for row in estimator.trace}

    rows: list[dict[str, Any]] = []
    for idx, t in enumerate(time_s):
        imu_ang = [float(x) for x in imu_log.AngVelPlatform[idx]]
        tam_vec = [float(x) for x in tam_log.tam_S[idx]]
        erow = est_trace.get(round(float(t), 9), {})
        rows.append({
            "time_s": float(t),
            "fusion_mode": cfg.fusion_mode,
            "attitude_error_norm": attitude_error_norm[idx],
            "rate_error_norm_rad_s": rate_error_norm[idx],
            "rw_speed_0_rad_s": float(rw_speeds[idx][0]),
            "rw_speed_1_rad_s": float(rw_speeds[idx][1]) if n_wheels > 1 else 0.0,
            "rw_speed_2_rad_s": float(rw_speeds[idx][2]) if n_wheels > 2 else 0.0,
            "imu_ang_vel_0_rad_s": imu_ang[0],
            "imu_ang_vel_1_rad_s": imu_ang[1],
            "imu_ang_vel_2_rad_s": imu_ang[2],
            "star_tracker_q_0": float(st_log.qInrtl2Case[idx][0]),
            "star_tracker_q_1": float(st_log.qInrtl2Case[idx][1]),
            "star_tracker_q_2": float(st_log.qInrtl2Case[idx][2]),
            "star_tracker_q_3": float(st_log.qInrtl2Case[idx][3]),
            "css_output": float(css_log.OutputData[idx]),
            "tam_0_t": tam_vec[0], "tam_1_t": tam_vec[1], "tam_2_t": tam_vec[2],
            "st_dropout": float(erow.get("st_dropout", 0.0)),
            "nav_innovation_norm": float(erow.get("nav_innovation_norm", 0.0)),
            "estimator_quality": float(erow.get("estimator_quality", 1.0)),
            "bias_est_norm_rad_s": float(erow.get("bias_est_norm_rad_s", 0.0)),
            "imu_injected_bias_norm_rad_s": float(erow.get("imu_injected_bias_norm_rad_s", 0.0)),
            "imu_bias_drift_norm_rad_s2": float(erow.get("imu_bias_drift_norm_rad_s2", 0.0)),
            "imu_random_walk_norm_rad_s": float(erow.get("imu_random_walk_norm_rad_s", 0.0)),
        })

    critical_series = {
        "attitude_error_norm": attitude_error_norm,
        "rate_error_norm_rad_s": rate_error_norm,
        "rw_speed_rad_s": [x for row in rw_speeds for x in row],
    }
    finite_ok = all(_is_finite_scalar(v) for values in critical_series.values() for v in values)

    initial_att = attitude_error_norm[0] if attitude_error_norm and _is_finite_scalar(attitude_error_norm[0]) else float("inf")
    final_att = attitude_error_norm[-1] if attitude_error_norm and _is_finite_scalar(attitude_error_norm[-1]) else float("inf")
    peak_att = max(attitude_error_norm) if attitude_error_norm and all(_is_finite_scalar(v) for v in attitude_error_norm) else float("inf")
    initial_rate = rate_error_norm[0] if rate_error_norm and _is_finite_scalar(rate_error_norm[0]) else float("inf")
    final_rate = rate_error_norm[-1] if rate_error_norm and _is_finite_scalar(rate_error_norm[-1]) else float("inf")
    final_ratio = final_att / max(initial_att, 1e-12) if finite_ok else float("inf")
    peak_ratio = peak_att / max(initial_att, 1e-12) if finite_ok else float("inf")
    rate_ratio = final_rate / max(initial_rate, 1e-12) if finite_ok else float("inf")
    qualities = [float(r["estimator_quality"]) for r in rows if _is_finite_scalar(r.get("estimator_quality", 0.0))]
    innovations = [float(r["nav_innovation_norm"]) for r in rows if _is_finite_scalar(r.get("nav_innovation_norm", 0.0))]
    status = "PASS" if finite_ok and len(rows) >= 3 and final_ratio <= cfg.max_final_attitude_ratio and final_att <= cfg.max_final_attitude_error_norm else "FAIL"
    summary = SensorFusionSummary(
        backend="basilisk_native_adcs_sensor_fusion",
        subsystem="adcs",
        basilisk_simbase_used=True,
        execute_simulation_used=True,
        sensor_fusion_in_loop=True,
        estimator_type={"ideal":"ideal_st_imu_fusion_baseline","noisy_dropout":"st_imu_noisy_dropout_stress","mekf_prototype":"mekf_style_st_imu_prototype"}[cfg.fusion_mode],
        native_modules=("spacecraft.Spacecraft","imuSensor.ImuSensor","starTracker.StarTracker","coarseSunSensor.CoarseSunSensor","magnetometer.Magnetometer","inertial3D.inertial3D","attTrackingError.attTrackingError","mrpFeedback.mrpFeedback","rwMotorTorque.rwMotorTorque","reactionWheelStateEffector.ReactionWheelStateEffector"),
        custom_modules=("StarTrackerImuFusionEstimator(SysModel)",),
        sample_count=len(rows),
        initial_attitude_error_norm=float(initial_att),
        final_attitude_error_norm=float(final_att),
        peak_attitude_error_norm=float(peak_att),
        final_attitude_error_ratio=float(final_ratio),
        peak_attitude_error_ratio=float(peak_ratio),
        initial_rate_error_norm_rad_s=float(initial_rate),
        final_rate_error_norm_rad_s=float(final_rate),
        final_rate_error_ratio=float(rate_ratio),
        estimator_quality_min=float(min(qualities) if qualities else 0.0),
        st_dropout_sample_count=int(sum(1 for r in rows if float(r["st_dropout"]) > 0.5)),
        max_nav_innovation_norm=float(max(innovations) if innovations else 0.0),
        status=status,
        not_claimed=("flight_validated_EKF_or_MEKF","CSS_TAM_closed_loop_navigation_update","full_mode_aware_ADCS_closed_loop","external_validation"),
    )
    return summary, tuple(rows)


def run_ideal_sensor_fusion_baseline() -> tuple[SensorFusionSummary, tuple[dict[str, Any], ...]]:
    return _run_sensor_fusion(SensorFusionConfig(fusion_mode="ideal"))


def run_noisy_dropout_stress() -> tuple[SensorFusionSummary, tuple[dict[str, Any], ...]]:
    return _run_sensor_fusion(SensorFusionConfig(
        fusion_mode="noisy_dropout",
        attitude_noise_mrp=(3.0e-4, -2.0e-4, 2.5e-4),
        imu_rate_bias_rad_s=(1.0e-5, -1.5e-5, 1.2e-5),
        st_dropout_start_s=80.0,
        st_dropout_end_s=130.0,
        alpha_st=0.55,
        max_final_attitude_ratio=0.45,
        max_final_attitude_error_norm=0.16,
    ))


def run_mekf_style_prototype() -> tuple[SensorFusionSummary, tuple[dict[str, Any], ...]]:
    return _run_sensor_fusion(SensorFusionConfig(
        fusion_mode="mekf_prototype",
        attitude_noise_mrp=(2.5e-4, -1.5e-4, 2.0e-4),
        imu_rate_bias_rad_s=(8.0e-6, -1.0e-5, 8.0e-6),
        st_dropout_start_s=-1.0,
        st_dropout_end_s=-1.0,
        mekf_gain=0.85,
        mekf_bias_gain=0.0,
        max_final_attitude_ratio=0.45,
        max_final_attitude_error_norm=0.16,
    ))


def write_sensor_fusion_dataset(output_dir: str | Path, mode: FusionMode = "ideal") -> dict[str, str]:
    output_dir = Path(output_dir); output_dir.mkdir(parents=True, exist_ok=True)
    if mode == "ideal":
        summary, rows = run_ideal_sensor_fusion_baseline()
        stem = "adcs_ideal_sensor_fusion"
    elif mode == "noisy_dropout":
        summary, rows = run_noisy_dropout_stress()
        stem = "adcs_noisy_dropout_stress"
    elif mode == "mekf_prototype":
        summary, rows = run_mekf_style_prototype()
        stem = "adcs_mekf_style_prototype"
    else:
        raise ValueError(f"unsupported fusion mode: {mode}")
    summary_path = output_dir / f"{stem}_summary.json"
    trace_path = output_dir / f"{stem}_trace.csv"
    manifest_path = output_dir / f"{stem}_manifest.json"
    summary_path.write_text(json.dumps(asdict(summary), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = list(rows[0].keys()) if rows else []
    with trace_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    manifest = {"dataset_type": f"basilisk_native_adcs_{mode}", "backend_truth": "Basilisk SimBaseClass + ST/IMU fusion estimator SysModel + RW closed loop", "files": {"summary": summary_path.name, "trace": trace_path.name}, "summary": asdict(summary)}
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"summary": str(summary_path), "trace": str(trace_path), "manifest": str(manifest_path)}


def write_all_sensor_fusion_datasets(output_root: str | Path) -> dict[str, dict[str, str]]:
    output_root = Path(output_root); output_root.mkdir(parents=True, exist_ok=True)
    return {
        "ideal": write_sensor_fusion_dataset(output_root / "ideal_sensor_fusion", "ideal"),
        "noisy_dropout": write_sensor_fusion_dataset(output_root / "noisy_dropout_stress", "noisy_dropout"),
        "mekf_prototype": write_sensor_fusion_dataset(output_root / "mekf_style_prototype", "mekf_prototype"),
    }


if __name__ == "__main__":  # pragma: no cover
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", default="datasets/basilisk_native_adcs_sensor_fusion")
    parser.add_argument("--mode", choices=["ideal", "noisy_dropout", "mekf_prototype", "all"], default="all")
    args = parser.parse_args()
    if args.mode == "all":
        print(json.dumps(write_all_sensor_fusion_datasets(args.output_dir), indent=2, ensure_ascii=False))
    else:
        print(json.dumps(write_sensor_fusion_dataset(args.output_dir, args.mode), indent=2, ensure_ascii=False))
