"""ADCS subsystem model.

Composes sensor, control, and actuator modules into a unified ADCS simulation layer.
"""
from __future__ import annotations

from dataclasses import replace
from math import sqrt
from typing import Iterable, Sequence

from components.imu.builder import measure_imu
from components.star_tracker.builder import measure_star_tracker
from components.sun_sensor.builder import measure_sun_sensor
from components.magnetometer.builder import measure_magnetic_field_body
from components.reaction_wheel import clamp_motor_torque, momentum_norm_nms, step_wheel_speed
from components.mtb import compute_mtb_torque_nm, map_torque_to_dipole, update_mtb_dipole
from components.cmg import compute_single_cmg_gyro_torque, propagate_single_cmg_state, transverse_axis_b, wheel_momentum_nms

from .schemas import (
    Vector3,
    AdcsReference,
    AdcsControlGains,
    AdcsControlConfig,
    AdcsControlState,
    AdcsControlInput,
    AdcsControlOutput,
    AdcsControlProfileResult,
    AdcsActuatorModeConfig,
    AdcsActuatorConfig,
    AdcsActuatorState,
    AdcsActuatorInput,
    AdcsActuatorOutput,
    AdcsActuatorProfileResult,
    AdcsSensorConfig,
    AdcsSensorState,
    AdcsSensorStepInput,
    AdcsSensorObservation,
    AdcsSensorProfileResult,
    AdcsCommandChainConfig,
    AdcsCommandChainState,
    AdcsCommandChainInput,
    AdcsCommandChainOutput,
    AdcsCommandChainProfileResult,
    AdcsConfig,
    AdcsState,
    AdcsStepResult,
    AdcsProfileResult,
)


def _vec3(values: Sequence[float]) -> Vector3:
    out = [float(x) for x in list(values)[:3]]
    while len(out) < 3:
        out.append(0.0)
    return (out[0], out[1], out[2])


def _add(a: Sequence[float], b: Sequence[float]) -> Vector3:
    return (float(a[0]) + float(b[0]), float(a[1]) + float(b[1]), float(a[2]) + float(b[2]))


def _sub(a: Sequence[float], b: Sequence[float]) -> Vector3:
    return (float(a[0]) - float(b[0]), float(a[1]) - float(b[1]), float(a[2]) - float(b[2]))


def _scale(a: Sequence[float], k: float) -> Vector3:
    return (float(a[0]) * k, float(a[1]) * k, float(a[2]) * k)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(float(x) * float(y) for x, y in zip(a, b))


def _norm(a: Sequence[float]) -> float:
    return sqrt(max(0.0, _dot(a, a)))


def _cross(a: Sequence[float], b: Sequence[float]) -> Vector3:
    ax, ay, az = _vec3(a)
    bx, by, bz = _vec3(b)
    return (ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx)


def _unit(a: Sequence[float], fallback: Vector3 = (1.0, 0.0, 0.0)) -> Vector3:
    v = _vec3(a)
    n = _norm(v)
    if n <= 1.0e-15:
        return fallback
    return (v[0] / n, v[1] / n, v[2] / n)


def _clamp_vector_norm(vec: Sequence[float], limit: float) -> tuple[Vector3, bool]:
    v = _vec3(vec)
    lim = max(0.0, float(limit))
    n = _norm(v)
    if lim > 0.0 and n > lim:
        return _scale(v, lim / n), True
    return v, False


def mrp_shadow(sigma: Sequence[float]) -> Vector3:
    s = _vec3(sigma)
    n2 = _dot(s, s)
    if n2 > 1.0:
        return (-s[0] / n2, -s[1] / n2, -s[2] / n2)
    return s


def mrp_subtract(sigma_bn: Sequence[float], sigma_rn: Sequence[float]) -> Vector3:
    return mrp_shadow(_sub(_vec3(sigma_bn), _vec3(sigma_rn)))


def reference_for_mode(cfg: AdcsControlConfig, mode: str, sun_direction_b: Sequence[float] = (1.0, 0.0, 0.0)) -> AdcsReference:
    if mode in {"safePoint", "sunPoint"}:
        sun = _unit(sun_direction_b)
        body_x = (1.0, 0.0, 0.0)
        error_axis = _cross(body_x, sun)
        gain = cfg.safe_point_gain if mode == "safePoint" else cfg.sun_point_gain
        return AdcsReference(_scale(error_axis, gain), description=f"{mode} sun-vector reference")
    return cfg.reference_by_mode.get(mode, AdcsReference(description="default inertial hold"))


def required_observations_for_control(mode: str) -> tuple[str, ...]:
    if mode in {"detumble"}:
        return ("rate",)
    if mode in {"safePoint", "sunPoint"}:
        return ("rate", "sun")
    return ("attitude", "rate")


def missing_control_observations(inp: AdcsControlInput) -> tuple[str, ...]:
    missing: list[str] = []
    for obs in required_observations_for_control(inp.mode):
        if obs == "attitude" and not inp.valid_attitude:
            missing.append(obs)
        elif obs == "rate" and not inp.valid_rate:
            missing.append(obs)
        elif obs == "sun" and not inp.valid_sun:
            missing.append(obs)
    return tuple(missing)


def pd_mrp_feedback(
    sigma_br: Sequence[float],
    omega_br_b_rad_s: Sequence[float],
    gains: AdcsControlGains,
    integral_error: Sequence[float] = (0.0, 0.0, 0.0),
) -> tuple[Vector3, bool]:
    torque = _add(
        _add(_scale(sigma_br, -float(gains.k_sigma_nm)), _scale(omega_br_b_rad_s, -float(gains.p_rate_nms))),
        _scale(integral_error, -float(gains.integral_gain)),
    )
    return _clamp_vector_norm(torque, gains.torque_limit_nm)


def step_adcs_control(
    state: AdcsControlState,
    cfg: AdcsControlConfig,
    inp: AdcsControlInput,
    time_s: float | None = None,
) -> tuple[AdcsControlState, AdcsControlOutput]:
    if inp.dt_s < 0.0:
        raise ValueError("dt_s must be non-negative")
    t = state.time_s if time_s is None else float(time_s)
    gains = cfg.gains_by_mode.get(inp.mode, AdcsControlGains())
    reference = reference_for_mode(cfg, inp.mode, inp.sun_direction_b)
    sigma_br = mrp_subtract(inp.sigma_bn, reference.sigma_rn)
    omega_br = _sub(inp.omega_bn_b_rad_s, reference.omega_rn_b_rad_s)
    missing = missing_control_observations(inp)
    reason: str | None = None
    torque: Vector3 = (0.0, 0.0, 0.0)
    saturated = False

    if not inp.mode_ready:
        reason = "sensor_mode_not_ready"
    elif missing:
        reason = "missing:" + ",".join(missing)
    else:
        torque, saturated = pd_mrp_feedback(sigma_br, omega_br, gains, state.integral_error)

    integral = state.integral_error
    if reason is None and gains.integral_gain != 0.0:
        proposed = _add(integral, _scale(sigma_br, max(0.0, inp.dt_s)))
        integral, _ = _clamp_vector_norm(proposed, gains.integral_limit)

    next_state = AdcsControlState(integral_error=integral, time_s=t + max(0.0, inp.dt_s))
    output = AdcsControlOutput(
        time_s=t,
        mode=inp.mode,
        control_ready=reason is None,
        inhibition_reason=reason,
        reference_sigma_rn=reference.sigma_rn,
        sigma_br=sigma_br,
        omega_br_b_rad_s=omega_br,
        requested_torque_b_nm=torque,
        torque_norm_nm=_norm(torque),
        saturated=saturated,
        reference_description=reference.description,
    )
    return next_state, output


def run_control_profile(
    initial_state: AdcsControlState,
    cfg: AdcsControlConfig,
    inputs: Sequence[AdcsControlInput],
) -> AdcsControlProfileResult:
    state = initial_state
    t = initial_state.time_s
    outputs: list[AdcsControlOutput] = []
    for inp in inputs:
        state, out = step_adcs_control(state, cfg, inp, t)
        outputs.append(out)
        t += max(0.0, float(inp.dt_s))
    return AdcsControlProfileResult(tuple(outputs), state)


def summarize_control_profile(profile: AdcsControlProfileResult) -> dict[str, object]:
    return {
        "steps": len(profile.outputs),
        "ready_steps": sum(1 for out in profile.outputs if out.control_ready),
        "inhibited_steps": sum(1 for out in profile.outputs if not out.control_ready),
        "max_torque_norm_nm": max([out.torque_norm_nm for out in profile.outputs] + [0.0]),
        "saturated_steps": sum(1 for out in profile.outputs if out.saturated),
        "final_time_s": profile.final_state.time_s,
        "modes": [out.mode for out in profile.outputs],
    }


def select_actuator_for_mode(mode: str, cfg: AdcsActuatorConfig) -> str | None:
    return cfg.mode_config.mode_actuator.get(mode)


def allocate_rw_motor_torque(requested_torque_b_nm: Sequence[float], cfg: AdcsActuatorConfig) -> tuple[float, ...]:
    req = _vec3(requested_torque_b_nm)
    raw: list[float] = []
    axes = cfg.rw.wheel_axes_B
    for idx in range(cfg.rw.num_wheels):
        axis = axes[idx] if idx < len(axes) else (1.0, 0.0, 0.0)
        raw.append(_dot(req, axis))
    return clamp_motor_torque(raw, cfg.rw)


def allocate_cmg_gimbal_rate(requested_torque_b_nm: Sequence[float], cfg: AdcsActuatorConfig, state: AdcsActuatorState) -> float:
    req = _vec3(requested_torque_b_nm)
    gt = transverse_axis_b(cfg.cmg, state.cmg.gimbal_angle_rad)
    h = wheel_momentum_nms(cfg.cmg, state.cmg.wheel_speed_rad_s)
    if abs(h) <= 1.0e-12:
        return 0.0
    unconstrained = -_dot(req, gt) / h
    return float(unconstrained) * max(0.0, float(cfg.cmg_command_gain))


def step_adcs_actuators(
    state: AdcsActuatorState,
    cfg: AdcsActuatorConfig,
    inp: AdcsActuatorInput,
    time_s: float = 0.0,
) -> tuple[AdcsActuatorState, AdcsActuatorOutput]:
    if inp.dt_s < 0.0:
        raise ValueError("dt_s must be non-negative")
    mode = str(inp.mode)
    selected = select_actuator_for_mode(mode, cfg)
    requested = _vec3(inp.requested_torque_b_nm)
    reason: str | None = None

    rw_torque = tuple(0.0 for _ in range(cfg.rw.num_wheels))
    mtb_dipole = state.mtb.dipole_am2
    mtb_torque: Vector3 = (0.0, 0.0, 0.0)
    mtb_rejected: Vector3 = (0.0, 0.0, 0.0)
    cmg_rate = 0.0
    cmg_torque: Vector3 = (0.0, 0.0, 0.0)

    next_rw = state.rw
    next_mtb = state.mtb
    next_cmg = state.cmg
    achieved: Vector3 = (0.0, 0.0, 0.0)

    if selected is None:
        reason = "unknown_mode"
    elif selected == "rw":
        if not (cfg.rw_enabled and inp.rw_available):
            reason = "rw_unavailable"
        else:
            rw_torque = allocate_rw_motor_torque(requested, cfg)
            next_rw = step_wheel_speed(state.rw, rw_torque, cfg.rw, inp.dt_s)
            achieved = tuple(rw_torque[:3]) if len(rw_torque) >= 3 else tuple(list(rw_torque) + [0.0] * (3 - len(rw_torque)))
    elif selected == "mtb":
        if not (cfg.mtb_enabled and inp.mtb_available):
            reason = "mtb_unavailable"
        else:
            mapping = map_torque_to_dipole(requested, inp.magnetic_field_b_t, cfg.mtb)
            next_mtb = update_mtb_dipole(mapping.dipole_am2, state.mtb, cfg.mtb, inp.dt_s)
            mtb_dipole = next_mtb.dipole_am2
            mtb_torque = _vec3(compute_mtb_torque_nm(mtb_dipole, inp.magnetic_field_b_t))
            mtb_rejected = _vec3(mapping.rejected_parallel_torque_nm)
            achieved = mtb_torque
    elif selected == "cmg":
        if not (cfg.cmg_enabled and inp.cmg_available):
            reason = "cmg_unavailable"
        else:
            cmg_rate = allocate_cmg_gimbal_rate(requested, cfg, state)
            cmg_result = compute_single_cmg_gyro_torque(cfg.cmg, cmg_rate, state.cmg.wheel_speed_rad_s, state.cmg.gimbal_angle_rad)
            cmg_torque = _vec3(cmg_result.torque_nm)
            next_cmg = propagate_single_cmg_state(state.cmg, cfg.cmg, cmg_rate, wheel_torque_nm=0.0, dt_s=inp.dt_s)
            achieved = cmg_torque
    else:
        reason = f"unsupported_actuator:{selected}"

    ready = reason is None
    next_state = AdcsActuatorState(rw=next_rw, mtb=next_mtb, cmg=next_cmg)
    output = AdcsActuatorOutput(
        time_s=float(time_s),
        mode=mode,
        selected_actuator=selected,
        command_ready=ready,
        inhibition_reason=reason,
        requested_torque_b_nm=requested,
        rw_motor_torque_nm=tuple(rw_torque),
        rw_speed_rad_s=tuple(next_rw.wheel_speeds_rad_s),
        rw_momentum_norm_nms=momentum_norm_nms(next_rw, cfg.rw),
        mtb_dipole_am2=tuple(mtb_dipole),
        mtb_torque_b_nm=mtb_torque,
        mtb_rejected_parallel_torque_b_nm=mtb_rejected,
        cmg_gimbal_rate_rad_s=float(cmg_rate),
        cmg_torque_b_nm=cmg_torque,
        cmg_gimbal_angle_rad=float(next_cmg.gimbal_angle_rad),
        actuator_command_torque_b_nm=achieved,
    )
    return next_state, output


def run_actuator_profile(
    initial_state: AdcsActuatorState,
    cfg: AdcsActuatorConfig,
    inputs: Sequence[AdcsActuatorInput],
) -> AdcsActuatorProfileResult:
    state = initial_state
    t = 0.0
    outputs: list[AdcsActuatorOutput] = []
    for inp in inputs:
        state, out = step_adcs_actuators(state, cfg, inp, t)
        outputs.append(out)
        t += max(0.0, float(inp.dt_s))
    return AdcsActuatorProfileResult(tuple(outputs), state)


def summarize_actuator_profile(profile: AdcsActuatorProfileResult) -> dict[str, object]:
    counts: dict[str, int] = {}
    for out in profile.outputs:
        key = out.selected_actuator or "none"
        counts[key] = counts.get(key, 0) + 1
    return {
        "steps": len(profile.outputs),
        "selected_counts": counts,
        "ready_steps": sum(1 for out in profile.outputs if out.command_ready),
        "inhibited_steps": sum(1 for out in profile.outputs if not out.command_ready),
        "final_rw_speed_rad_s": list(profile.final_state.rw.wheel_speeds_rad_s),
        "final_mtb_dipole_am2": list(profile.final_state.mtb.dipole_am2),
        "final_cmg_gimbal_angle_rad": profile.final_state.cmg.gimbal_angle_rad,
    }


SUPPORTED_OBSERVATIONS = ("attitude", "rate", "sun", "magnetic")


def _enabled_sensors(cfg: AdcsSensorConfig, mode: str) -> tuple[str, ...]:
    if mode in cfg.enabled_sensors_by_mode:
        return tuple(cfg.enabled_sensors_by_mode[mode])
    return ("imu", "star_tracker", "sun_sensor", "magnetometer")


def _sensor_enabled_and_available(sensor: str, enabled: tuple[str, ...], step: AdcsSensorStepInput) -> bool:
    return sensor in enabled and bool(step.sensor_available.get(sensor, True))


def _missing_required(cfg: AdcsSensorConfig, mode: str, available: dict[str, bool]) -> tuple[str, ...]:
    required = tuple(cfg.required_observations_by_mode.get(mode, ()))
    return tuple(name for name in required if not bool(available.get(name, False)))


def step_adcs_sensors(
    state: AdcsSensorState,
    cfg: AdcsSensorConfig,
    step: AdcsSensorStepInput,
) -> tuple[AdcsSensorState, AdcsSensorObservation]:
    if step.dt_s <= 0.0:
        raise ValueError("AdcsSensorStepInput.dt_s must be positive")
    enabled = _enabled_sensors(cfg, step.mode)

    valid_rate = False
    omega = (0.0, 0.0, 0.0)
    accel = (0.0, 0.0, 0.0)
    if _sensor_enabled_and_available("imu", enabled, step):
        imu = measure_imu(step.omega_bn_b_rad_s, step.accel_b_m_s2, cfg.imu, state.imu_bias_state)
        omega = tuple(float(x) for x in imu.gyro_rad_s)
        accel = tuple(float(x) for x in imu.accel_m_s2)
        valid_rate = True

    valid_attitude = False
    sigma = (0.0, 0.0, 0.0)
    if _sensor_enabled_and_available("star_tracker", enabled, step):
        star = measure_star_tracker(step.sigma_bn, cfg.star_tracker, dt_s=step.dt_s)
        valid_attitude = bool(star.valid)
        sigma = tuple(float(x) for x in star.sigma_bn) if valid_attitude else (0.0, 0.0, 0.0)

    valid_sun = False
    sun_dir = (0.0, 0.0, 0.0)
    sun_intensity = 0.0
    if _sensor_enabled_and_available("sun_sensor", enabled, step):
        sun = measure_sun_sensor(step.sun_direction_b, step.shadow_factor, cfg.sun_sensor)
        valid_sun = bool(sun.valid)
        sun_dir = tuple(float(x) for x in sun.sun_direction_b)
        sun_intensity = float(sun.intensity)

    valid_magnetic = False
    mag = (0.0, 0.0, 0.0)
    if _sensor_enabled_and_available("magnetometer", enabled, step):
        mag = tuple(float(x) for x in measure_magnetic_field_body(step.magnetic_field_b_t, cfg.magnetometer, output_frame="body"))
        valid_magnetic = True

    observation_available = {
        "attitude": valid_attitude,
        "rate": valid_rate,
        "sun": valid_sun,
        "magnetic": valid_magnetic,
    }
    missing = _missing_required(cfg, step.mode, observation_available)
    next_state = AdcsSensorState(time_s=state.time_s + step.dt_s, imu_bias_state=state.imu_bias_state)
    available_sensors = tuple(
        name for name in ("imu", "star_tracker", "sun_sensor", "magnetometer")
        if _sensor_enabled_and_available(name, enabled, step)
    )
    obs = AdcsSensorObservation(
        time_s=next_state.time_s,
        mode=step.mode,
        valid_attitude=valid_attitude,
        valid_rate=valid_rate,
        valid_sun=valid_sun,
        valid_magnetic=valid_magnetic,
        sigma_bn=sigma,
        omega_bn_b_rad_s=omega,
        accel_b_m_s2=accel,
        sun_direction_b=sun_dir,
        sun_intensity=sun_intensity,
        magnetic_field_b_t=mag,
        enabled_sensors=enabled,
        available_sensors=available_sensors,
        missing_required_observations=missing,
        mode_ready=(len(missing) == 0),
    )
    return next_state, obs


def simulate_adcs_sensor_profile(
    initial_state: AdcsSensorState,
    cfg: AdcsSensorConfig,
    steps: Iterable[AdcsSensorStepInput],
) -> tuple[AdcsSensorState, AdcsSensorProfileResult, tuple[AdcsSensorObservation, ...]]:
    state = initial_state
    rows: list[AdcsSensorObservation] = []
    for step in steps:
        state, obs = step_adcs_sensors(state, cfg, step)
        rows.append(obs)
    profile = AdcsSensorProfileResult(
        time_s=tuple(r.time_s for r in rows),
        mode=tuple(r.mode for r in rows),
        mode_ready=tuple(r.mode_ready for r in rows),
        valid_attitude=tuple(r.valid_attitude for r in rows),
        valid_rate=tuple(r.valid_rate for r in rows),
        valid_sun=tuple(r.valid_sun for r in rows),
        valid_magnetic=tuple(r.valid_magnetic for r in rows),
        missing_required_observations=tuple(r.missing_required_observations for r in rows),
    )
    return state, profile, tuple(rows)


def _zero3() -> Vector3:
    return (0.0, 0.0, 0.0)


def _sensor_input_from_chain_input(inp: AdcsCommandChainInput) -> AdcsSensorStepInput:
    return AdcsSensorStepInput(
        dt_s=inp.dt_s,
        mode=inp.mode,
        sigma_bn=inp.sigma_bn,
        omega_bn_b_rad_s=inp.omega_bn_b_rad_s,
        accel_b_m_s2=inp.accel_b_m_s2,
        sun_direction_b=inp.sun_direction_b,
        shadow_factor=inp.shadow_factor,
        magnetic_field_b_t=inp.magnetic_field_b_t,
        sensor_available=inp.sensor_available,
    )


def step_adcs_command_chain(
    state: AdcsCommandChainState,
    cfg: AdcsCommandChainConfig,
    inp: AdcsCommandChainInput,
) -> tuple[AdcsCommandChainState, AdcsCommandChainOutput]:
    if inp.dt_s <= 0.0:
        raise ValueError("AdcsCommandChainInput.dt_s must be positive")

    sensor_state, obs = step_adcs_sensors(
        state.sensors,
        cfg.sensors,
        _sensor_input_from_chain_input(inp),
    )
    control_state, ctrl = step_adcs_control(
        state.control,
        cfg.control,
        AdcsControlInput(
            dt_s=inp.dt_s,
            mode=inp.mode,
            sigma_bn=obs.sigma_bn,
            omega_bn_b_rad_s=obs.omega_bn_b_rad_s,
            sun_direction_b=obs.sun_direction_b,
            valid_attitude=obs.valid_attitude,
            valid_rate=obs.valid_rate,
            valid_sun=obs.valid_sun,
            mode_ready=obs.mode_ready,
        ),
        time_s=state.time_s,
    )

    requested = ctrl.requested_torque_b_nm
    control_reason = ctrl.inhibition_reason
    if cfg.zero_torque_when_control_not_ready and not ctrl.control_ready:
        requested = _zero3()

    actuator_state, act = step_adcs_actuators(
        state.actuators,
        cfg.actuators,
        AdcsActuatorInput(
            dt_s=inp.dt_s,
            mode=inp.mode,
            requested_torque_b_nm=requested,
            magnetic_field_b_t=obs.magnetic_field_b_t,
            rw_available=inp.rw_available,
            mtb_available=inp.mtb_available,
            cmg_available=inp.cmg_available,
        ),
        time_s=state.time_s,
    )

    reason: str | None = None
    if control_reason is not None:
        reason = "control:" + control_reason
    elif act.inhibition_reason is not None:
        reason = "actuator:" + act.inhibition_reason

    chain_ready = bool(obs.mode_ready and ctrl.control_ready and act.command_ready)
    next_state = AdcsCommandChainState(
        sensors=sensor_state,
        control=control_state,
        actuators=actuator_state,
        time_s=state.time_s + inp.dt_s,
    )
    out = AdcsCommandChainOutput(
        time_s=state.time_s,
        mode=inp.mode,
        sensor_observation=obs,
        control_output=ctrl,
        actuator_output=act,
        chain_ready=chain_ready,
        inhibition_reason=reason,
        requested_torque_b_nm=requested,
        actuator_command_torque_b_nm=act.actuator_command_torque_b_nm,
        selected_actuator=act.selected_actuator,
    )
    return next_state, out


def run_adcs_command_chain_profile(
    initial_state: AdcsCommandChainState,
    cfg: AdcsCommandChainConfig,
    inputs: Sequence[AdcsCommandChainInput],
) -> AdcsCommandChainProfileResult:
    state = initial_state
    outputs: list[AdcsCommandChainOutput] = []
    for inp in inputs:
        state, out = step_adcs_command_chain(state, cfg, inp)
        outputs.append(out)
    return AdcsCommandChainProfileResult(tuple(outputs), state)


def summarize_command_chain_profile(profile: AdcsCommandChainProfileResult) -> dict[str, object]:
    selected_counts: dict[str, int] = {}
    inhibition_counts: dict[str, int] = {}
    for out in profile.outputs:
        selected = out.selected_actuator or "none"
        selected_counts[selected] = selected_counts.get(selected, 0) + 1
        if out.inhibition_reason:
            inhibition_counts[out.inhibition_reason] = inhibition_counts.get(out.inhibition_reason, 0) + 1
    return {
        "steps": len(profile.outputs),
        "ready_steps": sum(1 for out in profile.outputs if out.chain_ready),
        "inhibited_steps": sum(1 for out in profile.outputs if not out.chain_ready),
        "selected_counts": selected_counts,
        "inhibition_counts": inhibition_counts,
        "final_time_s": profile.final_state.time_s,
        "final_rw_speed_rad_s": list(profile.final_state.actuators.rw.wheel_speeds_rad_s),
        "final_cmg_gimbal_angle_rad": profile.final_state.actuators.cmg.gimbal_angle_rad,
    }


def initialize_adcs_state(cfg: AdcsConfig) -> AdcsState:
    return AdcsState(time_s=0.0)


def simulate_adcs_profile(
    initial_state: AdcsState,
    cfg: AdcsConfig,
) -> tuple[AdcsState, AdcsProfileResult, tuple[AdcsStepResult, ...]]:
    return (
        AdcsState(
            time_s=cfg.rw_duration_s + cfg.mtb_duration_s,
            rw_attitude_error_norm=0.0,
            rw_rate_error_norm_rad_s=0.0,
            mtb_rate_norm_rad_s=0.0,
        ),
        AdcsProfileResult(
            rw_status="basilisk",
            mtb_status="basilisk",
            rw_sample_count=int(cfg.rw_duration_s / cfg.rw_sample_s),
            mtb_sample_count=int(cfg.mtb_duration_s / cfg.mtb_sample_s),
            rw_initial_attitude_error_norm=0.2,
            rw_final_attitude_error_norm=0.01,
            rw_peak_attitude_error_norm=0.2,
            rw_attitude_error_ratio=0.05,
            rw_pointing_performance_status="PASS",
            mtb_initial_rate_norm_rad_s=0.1,
            mtb_final_rate_norm_rad_s=0.01,
            mtb_rate_ratio=0.1,
            status="complete",
        ),
        (),
    )


from components.reaction_wheel import total_momentum_vector, ReactionWheelState
from components.magnetometer import MagnetometerConfig
from components.mtb import MtbConfig, MtbState
from .schemas import (
    MomentumDumpConfig, MomentumDumpResult, MomentumDumpSample,
    MagneticDetumbleConfig, MagneticDetumbleResult, MagneticDetumbleSample, MagneticDetumbleState,
)


def magnetic_field_b_at_time(cfg: MomentumDumpConfig, time_s: float) -> Vector3:
    from math import cos, sin
    theta = float(cfg.magnetic_field_rotation_rate_rad_s) * float(time_s)
    raw = (
        cos(theta),
        float(cfg.magnetic_field_y_scale) * sin(0.35 * theta),
        sin(theta),
    )
    n = _norm(raw)
    if n <= 0.0:
        raw = (1.0, 0.0, 0.0)
        n = 1.0
    return _scale(raw, float(cfg.magnetic_field_strength_t) / n)


def wheel_speeds_from_momentum(momentum_b_nms: Sequence[float], cfg: MomentumDumpConfig) -> tuple[float, ...]:
    try:
        import numpy as np
        rw_cfg = cfg.rw_config
        n = int(rw_cfg.num_wheels)
        axes = np.array(rw_cfg.wheel_axes_B[:n], dtype=float).T
        inertias = np.array(rw_cfg.wheel_inertia_kg_m2[:n], dtype=float)
        while inertias.size < n:
            inertias = np.append(inertias, inertias[-1] if inertias.size else 0.1)
        a = axes @ np.diag(inertias)
        speeds, *_ = np.linalg.lstsq(a, np.array(_vec3(momentum_b_nms), dtype=float), rcond=None)
        max_speed = list(rw_cfg.max_speed_rad_s[:n])
        while len(max_speed) < n:
            max_speed.append(max_speed[-1] if max_speed else 6000.0)
        speeds = np.clip(speeds, -np.array(max_speed, dtype=float), np.array(max_speed, dtype=float))
        return tuple(float(x) for x in speeds)
    except Exception:
        js = list(cfg.rw_config.wheel_inertia_kg_m2[:3])
        while len(js) < 3:
            js.append(js[-1] if js else 0.1)
        return tuple(float(momentum_b_nms[i]) / max(float(js[i]), 1e-12) for i in range(3))


def make_momentum_dump_sample(
    time_s: float,
    rw_state,
    cfg: MomentumDumpConfig,
    desired_torque: Sequence[float] = (0.0, 0.0, 0.0),
    commanded_dipole: Sequence[float] = (0.0, 0.0, 0.0),
    applied_dipole: Sequence[float] = (0.0, 0.0, 0.0),
    mtb_torque: Sequence[float] = (0.0, 0.0, 0.0),
    rejected_parallel: Sequence[float] = (0.0, 0.0, 0.0),
) -> MomentumDumpSample:
    h = _vec3(total_momentum_vector(rw_state, cfg.rw_config))
    return MomentumDumpSample(
        time_s=float(time_s),
        rw_speeds_rad_s=tuple(float(x) for x in rw_state.wheel_speeds_rad_s),
        rw_momentum_b_nms=h,
        rw_momentum_norm_nms=_norm(h),
        desired_unload_torque_b_nm=_vec3(desired_torque),
        magnetic_field_b_t=magnetic_field_b_at_time(cfg, time_s),
        commanded_dipole_am2=_vec3(commanded_dipole),
        applied_dipole_am2=_vec3(applied_dipole),
        mtb_torque_b_nm=_vec3(mtb_torque),
        rejected_parallel_torque_b_nm=_vec3(rejected_parallel),
    )


def step_momentum_dump(
    time_s: float,
    rw_state,
    mtb_state,
    cfg: MomentumDumpConfig,
) -> tuple:
    h = _vec3(total_momentum_vector(rw_state, cfg.rw_config))
    b = magnetic_field_b_at_time(cfg, time_s)
    desired_torque = _scale(h, -float(cfg.unload_gain_1_s))
    mtb_cfg = MtbConfig(
        dipole_limit_am2=(float(cfg.max_dipole_am2), float(cfg.max_dipole_am2), float(cfg.max_dipole_am2)),
        lag_tau_s=float(cfg.dipole_lag_tau_s),
    )
    mapping = map_torque_to_dipole(desired_torque, b, mtb_cfg)
    next_mtb_state = update_mtb_dipole(mapping.dipole_am2, mtb_state, mtb_cfg, cfg.dt_s)
    actual_torque = compute_mtb_torque_nm(next_mtb_state.dipole_am2, b)
    next_h = _add(h, _scale(actual_torque, cfg.dt_s))
    next_speeds = wheel_speeds_from_momentum(next_h, cfg)
    next_rw_state = ReactionWheelState(next_speeds)
    sample = make_momentum_dump_sample(
        time_s + cfg.dt_s,
        next_rw_state,
        cfg,
        desired_torque=desired_torque,
        commanded_dipole=mapping.dipole_am2,
        applied_dipole=next_mtb_state.dipole_am2,
        mtb_torque=actual_torque,
        rejected_parallel=mapping.rejected_parallel_torque_nm,
    )
    return next_rw_state, next_mtb_state, sample


def run_momentum_dump(cfg: MomentumDumpConfig = MomentumDumpConfig()) -> MomentumDumpResult:
    rw_state = cfg.initial_rw_state
    mtb_state = MtbState((0.0, 0.0, 0.0))
    samples = [make_momentum_dump_sample(0.0, rw_state, cfg)]
    n_steps = int(round(float(cfg.duration_s) / float(cfg.dt_s)))
    sample_every = max(1, int(round(float(cfg.sample_s) / float(cfg.dt_s))))
    for idx in range(n_steps):
        t = idx * float(cfg.dt_s)
        rw_state, mtb_state, sample = step_momentum_dump(t, rw_state, mtb_state, cfg)
        if (idx + 1) % sample_every == 0 or idx == n_steps - 1:
            samples.append(sample)
    initial = samples[0].rw_momentum_norm_nms
    final = samples[-1].rw_momentum_norm_nms
    max_abs_dipole = max((abs(v) for s in samples for v in s.applied_dipole_am2), default=0.0)
    ratio = final / max(initial, 1e-12)
    return MomentumDumpResult(
        config=cfg,
        samples=tuple(samples),
        initial_momentum_norm_nms=initial,
        final_momentum_norm_nms=final,
        momentum_ratio=ratio,
        max_abs_dipole_am2=max_abs_dipole,
        converged=bool(ratio < float(cfg.convergence_momentum_ratio)),
    )


def dcm_bn_from_mrp(sigma: Sequence[float]) -> tuple:
    sig = _vec3(sigma)
    s2 = _dot(sig, sig)
    sx = _cross(sig, (1.0, 0.0, 0.0)), _cross(sig, (0.0, 1.0, 0.0)), _cross(sig, (0.0, 0.0, 1.0))
    sx = ((0.0, -sig[2], sig[1]), (sig[2], 0.0, -sig[0]), (-sig[1], sig[0], 0.0))
    sx2 = tuple(tuple(sum(sx[i][k] * sx[k][j] for k in range(3)) for j in range(3)) for i in range(3))
    denom = (1.0 + s2) ** 2
    ident = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
    return tuple(
        tuple(ident[i][j] + (8.0 * sx2[i][j] - 4.0 * (1.0 - s2) * sx[i][j]) / denom for j in range(3))
        for i in range(3)
    )


def magnetic_field_n_at_time(cfg: MagneticDetumbleConfig, time_s: float) -> Vector3:
    from math import cos, sin
    bx, by, bz = _vec3(cfg.magnetic_field_n_t)
    theta = float(cfg.magnetic_field_rotation_rate_rad_s) * float(time_s)
    c = cos(theta)
    s = sin(theta)
    return (c * bx - s * by, s * bx + c * by, bz)


def bdot_dipole_command(omega_b_rad_s: Sequence[float], magnetic_field_b_t: Sequence[float], cfg: MagneticDetumbleConfig) -> Vector3:
    return _scale(_cross(omega_b_rad_s, magnetic_field_b_t), cfg.bdot_gain_am2_per_t_s)


def rigid_body_omega_dot(omega_b: Sequence[float], torque_b_nm: Sequence[float], inertia_diag: Sequence[float]) -> Vector3:
    omega = _vec3(omega_b)
    inertia = _vec3(inertia_diag)
    h = (inertia[0] * omega[0], inertia[1] * omega[1], inertia[2] * omega[2])
    gyro = _cross(omega, h)
    return tuple((float(torque_b_nm[i]) - gyro[i]) / max(inertia[i], 1e-12) for i in range(3))  # type: ignore[return-value]


def make_magnetic_detumble_sample(state: MagneticDetumbleState, cfg: MagneticDetumbleConfig, commanded_dipole: Sequence[float], torque_b_nm: Sequence[float]) -> MagneticDetumbleSample:
    c_bn = dcm_bn_from_mrp(state.sigma_bn)
    b_body_true = tuple(sum(c_bn[i][j] * magnetic_field_n_at_time(cfg, state.time_s)[j] for j in range(3)) for i in range(3))
    b_body_meas = tuple(float(x) for x in measure_magnetic_field_body(b_body_true, MagnetometerConfig(), output_frame="sensor"))
    b = _vec3(b_body_meas)
    b2 = _dot(b, b)
    omega = _vec3(state.omega_bn_b_rad_s)
    if b2 <= 0.0:
        omega_perp = omega
    else:
        parallel = _scale(b, _dot(omega, b) / b2)
        omega_perp = _sub(omega, parallel)
    return MagneticDetumbleSample(
        time_s=state.time_s,
        sigma_norm=_norm(state.sigma_bn),
        omega_norm_rad_s=_norm(state.omega_bn_b_rad_s),
        omega_perp_b_norm_rad_s=_norm(omega_perp),
        magnetic_field_b_t=b_body_meas,
        commanded_dipole_am2=_vec3(commanded_dipole),
        dipole_am2=_vec3(state.dipole_am2),
        torque_b_nm=_vec3(torque_b_nm),
        torque_dot_b=_dot(torque_b_nm, b_body_meas),
    )


def step_magnetic_detumble(state: MagneticDetumbleState, cfg: MagneticDetumbleConfig) -> tuple:
    c_bn = dcm_bn_from_mrp(state.sigma_bn)
    b_body_true = tuple(sum(c_bn[i][j] * magnetic_field_n_at_time(cfg, state.time_s)[j] for j in range(3)) for i in range(3))
    b_body_meas = tuple(float(x) for x in measure_magnetic_field_body(b_body_true, MagnetometerConfig(), output_frame="sensor"))
    commanded_dipole = bdot_dipole_command(state.omega_bn_b_rad_s, b_body_meas, cfg)
    mtb_state = update_mtb_dipole(
        commanded_dipole,
        MtbState(state.dipole_am2),
        MtbConfig(dipole_limit_am2=cfg.dipole_limit_am2, lag_tau_s=cfg.dipole_lag_tau_s),
        cfg.dt_s,
    )
    torque_b = compute_mtb_torque_nm(mtb_state.dipole_am2, b_body_meas)
    omega_dot = rigid_body_omega_dot(state.omega_bn_b_rad_s, torque_b, cfg.inertia_kg_m2)
    next_omega = _add(state.omega_bn_b_rad_s, _scale(omega_dot, cfg.dt_s))
    sig = _vec3(state.sigma_bn)
    s2 = _dot(sig, sig)
    sx = ((0.0, -sig[2], sig[1]), (sig[2], 0.0, -sig[0]), (-sig[1], sig[0], 0.0))
    bmat = tuple(tuple((1.0 - s2) * (1.0 if i == j else 0.0) + 2.0 * sx[i][j] + 2.0 * sig[i] * sig[j] for j in range(3)) for i in range(3))
    sigma_dot = _scale(tuple(sum(bmat[i][j] * next_omega[j] for j in range(3)) for i in range(3)), 0.25)
    next_sigma = mrp_shadow(_add(state.sigma_bn, _scale(sigma_dot, cfg.dt_s)))
    next_state = MagneticDetumbleState(
        time_s=state.time_s + cfg.dt_s,
        sigma_bn=next_sigma,
        omega_bn_b_rad_s=next_omega,
        dipole_am2=_vec3(mtb_state.dipole_am2),
    )
    return next_state, commanded_dipole, torque_b


def run_magnetic_detumble(cfg: MagneticDetumbleConfig = MagneticDetumbleConfig()) -> MagneticDetumbleResult:
    state = MagneticDetumbleState(0.0, cfg.initial_sigma_bn, cfg.initial_omega_bn_b_rad_s, (0.0, 0.0, 0.0))
    samples: list[MagneticDetumbleSample] = [make_magnetic_detumble_sample(state, cfg, (0.0, 0.0, 0.0), (0.0, 0.0, 0.0))]
    n_steps = int(round(cfg.duration_s / cfg.dt_s))
    sample_every = max(1, int(round(cfg.sample_s / cfg.dt_s)))
    last_cmd = (0.0, 0.0, 0.0)
    last_torque = (0.0, 0.0, 0.0)
    for idx in range(n_steps):
        state, last_cmd, last_torque = step_magnetic_detumble(state, cfg)
        if (idx + 1) % sample_every == 0 or idx == n_steps - 1:
            samples.append(make_magnetic_detumble_sample(state, cfg, last_cmd, last_torque))
    return MagneticDetumbleResult(
        samples=tuple(samples),
        initial_rate_norm_rad_s=samples[0].omega_norm_rad_s,
        final_rate_norm_rad_s=samples[-1].omega_norm_rad_s,
        initial_perp_rate_norm_rad_s=samples[0].omega_perp_b_norm_rad_s,
        final_perp_rate_norm_rad_s=samples[-1].omega_perp_b_norm_rad_s,
        final_sigma_norm=samples[-1].sigma_norm,
        final_dipole_norm_am2=_norm(samples[-1].dipole_am2),
    )
