"""Project-owned SysModel modules executed inside the Basilisk scheduler.

These modules fill task-specific gaps that do not have a direct Basilisk stock
module.  They are not proxy/post-processing models: each module is scheduled in
the same Process/Task as the official dynamics, FSW, power, data and thermal
modules and communicates through Basilisk messages or mutates an explicitly
owned Basilisk model parameter before that model's scheduled update.
"""
from __future__ import annotations

import math
import random
from collections.abc import Sequence
from typing import Any

import numpy as np

from Basilisk.architecture import messaging, sysModel  # type: ignore
from Basilisk.utilities import RigidBodyKinematics, macros  # type: ignore

from .types import BSKEventSpec


def _seconds(nanos: int) -> float:
    return float(nanos) * macros.NANO2SEC


def _active(events: Sequence[BSKEventSpec], nanos: int, effects: set[str] | None = None) -> list[BSKEventSpec]:
    now = _seconds(nanos)
    return [event for event in events if event.active_at(now) and (effects is None or event.effect in effects)]


def _event_number(event: BSKEventSpec, names: Sequence[str], default: float) -> float:
    for name in names:
        value = event.parameters.get(name)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return float(default)


def _wheel_index(event: BSKEventSpec, wheel_count: int = 3) -> int:
    try:
        index = int(event.parameters.get("wheel_index", 0))
    except (TypeError, ValueError):
        index = 0
    return max(0, min(index, wheel_count - 1))


def _write_status(msg: Any, enabled: int, nanos: int, module_id: int) -> None:
    payload = messaging.DeviceStatusMsgPayload()
    payload.deviceStatus = int(enabled)
    msg.write(payload, nanos, module_id)


def _unit3(values: Sequence[float]) -> list[float]:
    vector = [float(value) for value in list(values)[:3]]
    magnitude = math.sqrt(sum(value * value for value in vector))
    return [value / magnitude for value in vector] if magnitude > 1.0e-12 else [0.0, 0.0, 0.0]


def _cross3(left: Sequence[float], right: Sequence[float]) -> list[float]:
    return [
        float(left[1]) * float(right[2]) - float(left[2]) * float(right[1]),
        float(left[2]) * float(right[0]) - float(left[0]) * float(right[2]),
        float(left[0]) * float(right[1]) - float(left[1]) * float(right[0]),
    ]


def _triad_dcm(body_1: Sequence[float], body_2: Sequence[float], ref_1: Sequence[float], ref_2: Sequence[float]) -> list[list[float]] | None:
    b1 = _unit3(body_1); b2 = _unit3(_cross3(b1, body_2)); b3 = _cross3(b1, b2)
    n1 = _unit3(ref_1); n2 = _unit3(_cross3(n1, ref_2)); n3 = _cross3(n1, n2)
    if min(sum(value * value for value in b2), sum(value * value for value in n2)) < 0.5:
        return None
    body = (b1, b2, b3); reference = (n1, n2, n3)
    return [[sum(body[column][row] * reference[column][axis] for column in range(3)) for axis in range(3)] for row in range(3)]


class NativeEventStatus(sysModel.SysModel):
    """Publish recorder-grounded event-category activity messages."""

    def __init__(self, events: Sequence[BSKEventSpec]) -> None:
        super().__init__()
        self.ModelTag = "project_native_event_status"
        self.events = tuple(events)
        self.faultOutMsg = messaging.DeviceStatusMsg()
        self.degradationOutMsg = messaging.DeviceStatusMsg()
        self.constraintOutMsg = messaging.DeviceStatusMsg()

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        active = _active(self.events, current_sim_nanos)
        _write_status(self.faultOutMsg, any(e.category == "fault" for e in active), current_sim_nanos, self.moduleID)
        _write_status(self.degradationOutMsg, any(e.category == "degradation" for e in active), current_sim_nanos, self.moduleID)
        _write_status(self.constraintOutMsg, any(e.category == "constraint" for e in active), current_sim_nanos, self.moduleID)


class AdcsSensorFusion(sysModel.SysModel):
    """Fuse star/IMU fine attitude with independent Sun/Earth-vector attitude."""

    def __init__(self) -> None:
        super().__init__()
        self.ModelTag = "project_adcs_sensor_fusion"
        self.starInMsg = messaging.STSensorMsgReader()
        self.imuInMsg = messaging.IMUSensorMsgReader()
        self.magInMsg = messaging.TAMSensorMsgReader()
        self.sunBodyInMsg = messaging.TAMSensorMsgReader()
        self.sunReferenceInMsg = messaging.TAMSensorMsgReader()
        self.earthBodyInMsg = messaging.TAMSensorMsgReader()
        self.earthReferenceInMsg = messaging.TAMSensorMsgReader()
        self.starValidInMsg = messaging.DeviceStatusMsgReader()
        self.sunValidInMsg = messaging.DeviceStatusMsgReader()
        self.attOutMsg = messaging.NavAttMsg()
        self.sourceModeOutMsg = messaging.DeviceStatusMsg()

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        star = self.starInMsg()
        imu = self.imuInMsg()
        _ = self.magInMsg()
        star_valid = bool(self.starValidInMsg().deviceStatus)
        sun_valid = bool(self.sunValidInMsg().deviceStatus)
        quaternion = [float(x) for x in star.qInrtl2Case]
        star_sigma = (
            list(RigidBodyKinematics.EP2MRP(quaternion))
            if star_valid and any(abs(value) > 0.0 for value in quaternion)
            else [0.0, 0.0, 0.0]
        )
        coarse_dcm = _triad_dcm(
            self.sunBodyInMsg().tam_S, self.earthBodyInMsg().tam_S,
            self.sunReferenceInMsg().tam_S, self.earthReferenceInMsg().tam_S,
        ) if sun_valid else None
        coarse_sigma = list(RigidBodyKinematics.EP2MRP(RigidBodyKinematics.C2EP(np.asarray(coarse_dcm)))) if coarse_dcm is not None else None
        if star_valid and coarse_sigma is not None:
            # Star tracker remains the fine solution; Sun/Earth TRIAD contributes
            # a bounded coarse correction and supplies an autonomous fallback.
            sigma = [0.9 * star_sigma[i] + 0.1 * coarse_sigma[i] for i in range(3)]
            source_mode = 3
        elif star_valid:
            sigma = star_sigma; source_mode = 2
        elif coarse_sigma is not None:
            sigma = coarse_sigma; source_mode = 1
        else:
            sigma = [0.0, 0.0, 0.0]; source_mode = 0
        payload = messaging.NavAttMsgPayload()
        payload.sigma_BN = sigma
        payload.omega_BN_B = [float(x) for x in imu.AngVelPlatform]
        payload.vehSunPntBdy = [float(value) for value in self.sunBodyInMsg().tam_S]
        payload.timeTag = _seconds(current_sim_nanos)
        self.attOutMsg.write(payload, current_sim_nanos, self.moduleID)
        _write_status(self.sourceModeOutMsg, source_mode, current_sim_nanos, self.moduleID)


class EarthHorizonSensor(sysModel.SysModel):
    """Publish the nadir/earth-centre direction in the spacecraft body frame.

    The project has no stock earth-sensor component.  This scheduled,
    message-driven module provides the ideal geometric measurement used by the
    dataset architecture; it is supplementary telemetry and is not fed into
    the star-tracker/IMU control loop.
    """

    def __init__(self) -> None:
        super().__init__()
        self.ModelTag = "project_earth_horizon_sensor"
        self.stateInMsg = messaging.SCStatesMsgReader()
        self.directionOutMsg = messaging.TAMSensorMsg()
        self.referenceOutMsg = messaging.TAMSensorMsg()

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        state = self.stateInMsg()
        position = [float(value) for value in state.r_BN_N]
        radius = math.sqrt(sum(value * value for value in position))
        direction_n = [-value / radius for value in position] if radius > 0.0 else [0.0, 0.0, 0.0]
        dcm_bn = RigidBodyKinematics.MRP2C([float(value) for value in state.sigma_BN])
        direction_b = [
            sum(float(dcm_bn[row][column]) * direction_n[column] for column in range(3))
            for row in range(3)
        ]
        payload = messaging.TAMSensorMsgPayload()
        payload.tam_S = direction_b
        self.directionOutMsg.write(payload, current_sim_nanos, self.moduleID)
        reference = messaging.TAMSensorMsgPayload(); reference.tam_S = direction_n
        self.referenceOutMsg.write(reference, current_sim_nanos, self.moduleID)


class SunDirectionSensor(sysModel.SysModel):
    """Publish simulated Sun direction in body and inertial frames."""

    def __init__(self) -> None:
        super().__init__()
        self.ModelTag = "project_sun_direction_sensor"
        self.stateInMsg = messaging.SCStatesMsgReader()
        self.sunInMsg = messaging.SpicePlanetStateMsgReader()
        self.bodyOutMsg = messaging.TAMSensorMsg()
        self.referenceOutMsg = messaging.TAMSensorMsg()

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        state = self.stateInMsg(); sun = self.sunInMsg()
        direction_n = _unit3([float(sun.PositionVector[i]) - float(state.r_BN_N[i]) for i in range(3)])
        dcm_bn = RigidBodyKinematics.MRP2C([float(value) for value in state.sigma_BN])
        direction_b = [sum(float(dcm_bn[row][column]) * direction_n[column] for column in range(3)) for row in range(3)]
        body = messaging.TAMSensorMsgPayload(); body.tam_S = direction_b
        reference = messaging.TAMSensorMsgPayload(); reference.tam_S = direction_n
        self.bodyOutMsg.write(body, current_sim_nanos, self.moduleID)
        self.referenceOutMsg.write(reference, current_sim_nanos, self.moduleID)


class StarTrackerFaultInjector(sysModel.SysModel):
    """Apply catalogued star-tracker failure signatures at the message boundary."""

    EFFECTS = {
        "adcs_star_tracker_signal_loss", "adcs_star_tracker_bias_drift",
        "adcs_star_tracker_accuracy_loss", "adcs_star_tracker_fov_obstruction",
        "adcs_star_tracker_stuck_at_last", "adcs_star_tracker_blinding",
        "adcs_star_tracker_dropout", "adcs_star_tracker_misalignment",
        "adcs_sensor_failure",
    }

    def __init__(self, events: Sequence[BSKEventSpec], *, seed: int = 0) -> None:
        super().__init__(); self.ModelTag = "project_star_tracker_fault_injector"
        self.events = tuple(event for event in events if event.effect in self.EFFECTS)
        self.starInMsg = messaging.STSensorMsgReader(); self.starOutMsg = messaging.STSensorMsg()
        self.validOutMsg = messaging.DeviceStatusMsg(); self._last = [1.0, 0.0, 0.0, 0.0]
        self._rng = random.Random(int(seed) + 701)

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        source = self.starInMsg(); quaternion = [float(value) for value in source.qInrtl2Case]
        active = _active(self.events, current_sim_nanos); valid = True; stuck = False
        sigma = list(RigidBodyKinematics.EP2MRP(quaternion)) if any(abs(value) > 0.0 for value in quaternion) else [0.0, 0.0, 0.0]
        for event in active:
            effect = event.effect
            if effect in {"adcs_star_tracker_signal_loss", "adcs_star_tracker_blinding", "adcs_star_tracker_dropout", "adcs_star_tracker_fov_obstruction", "adcs_sensor_failure"}:
                valid = False
            elif effect == "adcs_star_tracker_stuck_at_last":
                stuck = True
            elif effect in {"adcs_star_tracker_bias_drift", "adcs_star_tracker_misalignment"}:
                bias_deg = _event_number(event, ("bias_deg", "misalignment_deg"), 0.05)
                axis = int(_event_number(event, ("axis",), 0.0)) % 3
                sigma[axis] += math.tan(math.radians(bias_deg) / 4.0)
            elif effect == "adcs_star_tracker_accuracy_loss":
                noise_arcsec = _event_number(event, ("noise_arcsec",), 30.0)
                sigma = [value + math.tan(math.radians(self._rng.gauss(0.0, noise_arcsec / 3600.0)) / 4.0) for value in sigma]
        if stuck:
            quaternion = list(self._last)
        elif valid:
            quaternion = list(RigidBodyKinematics.MRP2EP(sigma)); self._last = list(quaternion)
        else:
            quaternion = [0.0, 0.0, 0.0, 0.0]
        payload = messaging.STSensorMsgPayload(); payload.qInrtl2Case = quaternion
        self.starOutMsg.write(payload, current_sim_nanos, self.moduleID)
        _write_status(self.validOutMsg, int(valid or stuck), current_sim_nanos, self.moduleID)


class SunVectorFaultInjector(sysModel.SysModel):
    """Apply coarse Sun-sensor catalog signatures to the vector measurement."""

    EFFECTS = {
        "adcs_sun_sensor_signal_loss", "adcs_sun_sensor_bias_drift",
        "adcs_sun_sensor_noise_increase", "adcs_sun_sensor_saturation",
        "adcs_sun_sensor_eclipse_blindness", "adcs_sun_sensor_false_eclipse",
        "adcs_sun_sensor_cell_failure", "adcs_sun_sensor_contamination",
    }

    def __init__(self, events: Sequence[BSKEventSpec], *, seed: int = 0) -> None:
        super().__init__(); self.ModelTag = "project_sun_vector_fault_injector"
        self.events = tuple(event for event in events if event.effect in self.EFFECTS)
        self.vectorInMsg = messaging.TAMSensorMsgReader(); self.vectorOutMsg = messaging.TAMSensorMsg()
        self.validOutMsg = messaging.DeviceStatusMsg(); self._rng = random.Random(int(seed) + 1701)

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        vector = [float(value) for value in self.vectorInMsg().tam_S]; valid = True
        for event in _active(self.events, current_sim_nanos):
            effect = event.effect
            if effect in {"adcs_sun_sensor_signal_loss", "adcs_sun_sensor_eclipse_blindness", "adcs_sun_sensor_false_eclipse"}:
                valid = False; vector = [0.0, 0.0, 0.0]
            elif effect == "adcs_sun_sensor_cell_failure":
                vector = [0.45 * value for value in vector]
            elif effect == "adcs_sun_sensor_contamination":
                vector = [0.60 * value for value in vector]; vector[0] += math.sin(math.radians(0.5))
            elif effect == "adcs_sun_sensor_bias_drift":
                vector[0] += math.sin(math.radians(_event_number(event, ("bias_deg",), 0.5)))
            elif effect == "adcs_sun_sensor_noise_increase":
                sigma_deg = _event_number(event, ("noise_deg",), 0.5)
                vector = [value + self._rng.gauss(0.0, math.sin(math.radians(sigma_deg))) for value in vector]
            elif effect == "adcs_sun_sensor_saturation":
                level = _event_number(event, ("saturation_level",), 0.35)
                vector = [max(-level, min(level, value)) for value in vector]
        payload = messaging.TAMSensorMsgPayload(); payload.tam_S = vector
        self.vectorOutMsg.write(payload, current_sim_nanos, self.moduleID)
        _write_status(self.validOutMsg, int(valid), current_sim_nanos, self.moduleID)


class ImuFaultInjector(sysModel.SysModel):
    """Apply gyro bias/noise events before the estimator consumes the IMU message."""

    EFFECTS = {
        "gyro_bias_step", "gyro_noise_increase", "adcs_imu_signal_loss",
        "adcs_imu_gyro_bias_drift", "adcs_imu_accel_bias_drift",
        "adcs_imu_gyro_noise_increase", "adcs_imu_accel_noise_increase",
        "adcs_imu_stuck_at_zero", "adcs_imu_axis_dropout", "adcs_imu_noise_burst",
    }

    def __init__(self, events: Sequence[BSKEventSpec], *, seed: int = 0, base_noise_std_rad_s: float = 1.0e-5) -> None:
        super().__init__()
        self.ModelTag = "project_imu_fault_injector"
        self.events = tuple(event for event in events if event.effect in self.EFFECTS)
        self.seed = int(seed)
        self.base_noise_std_rad_s = max(float(base_noise_std_rad_s), 0.0)
        self.imuInMsg = messaging.IMUSensorMsgReader()
        self.imuOutMsg = messaging.IMUSensorMsg()
        self.biasOutMsg = messaging.IMUSensorMsg()
        self.noiseOutMsg = messaging.IMUSensorMsg()
        self._rng = random.Random(self.seed)

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self._rng = random.Random(self.seed)

    @staticmethod
    def _vector_deg_s(value: Any) -> list[float]:
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes)):
            seq = list(value)[:3]
            while len(seq) < 3:
                seq.append(0.0)
            return [math.radians(float(x)) for x in seq]
        scalar = math.radians(float(value or 0.0))
        return [scalar, 0.0, 0.0]

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        source = self.imuInMsg()
        bias = [0.0, 0.0, 0.0]
        accel_bias = [0.0, 0.0, 0.0]
        noise_scale = 1.0
        accel_noise_scale = 1.0
        zero_all = False; dropout_axis: int | None = None
        now_s = _seconds(current_sim_nanos)
        for event in _active(self.events, current_sim_nanos):
            if event.effect == "gyro_bias_step":
                step = self._vector_deg_s(event.parameters.get("bias_step_deg_s", 0.05))
                bias = [left + right for left, right in zip(bias, step)]
            elif event.effect in {"gyro_noise_increase", "adcs_imu_gyro_noise_increase", "adcs_imu_noise_burst"}:
                noise_scale = max(noise_scale, _event_number(event, ("noise_scale",), 5.0))
            elif event.effect == "adcs_imu_gyro_bias_drift":
                axis = int(_event_number(event, ("axis",), 0.0)) % 3
                bias[axis] += math.radians(_event_number(event, ("drift_deg_s2",), 0.001)) * max(0.0, now_s - event.start_s)
            elif event.effect == "adcs_imu_accel_bias_drift":
                axis = int(_event_number(event, ("axis",), 0.0)) % 3
                accel_bias[axis] += _event_number(event, ("bias_m_s2",), 0.02)
            elif event.effect == "adcs_imu_accel_noise_increase":
                accel_noise_scale = max(accel_noise_scale, _event_number(event, ("noise_scale",), 8.0))
            elif event.effect in {"adcs_imu_signal_loss", "adcs_imu_stuck_at_zero"}:
                zero_all = True
            elif event.effect == "adcs_imu_axis_dropout":
                dropout_axis = int(_event_number(event, ("axis",), 0.0)) % 3
        noise_std = self.base_noise_std_rad_s * noise_scale if noise_scale > 1.0 else 0.0
        noise = [self._rng.gauss(0.0, noise_std) for _ in range(3)] if noise_std > 0.0 else [0.0, 0.0, 0.0]
        measured = [float(source.AngVelPlatform[i]) + bias[i] + noise[i] for i in range(3)]
        accel_noise_std = 1.0e-3 * accel_noise_scale if accel_noise_scale > 1.0 else 0.0
        accel_noise = [self._rng.gauss(0.0, accel_noise_std) for _ in range(3)] if accel_noise_std > 0.0 else [0.0, 0.0, 0.0]
        measured_accel = [float(source.AccelPlatform[i]) + accel_bias[i] + accel_noise[i] for i in range(3)]
        if zero_all:
            measured = [0.0, 0.0, 0.0]; measured_accel = [0.0, 0.0, 0.0]
        if dropout_axis is not None:
            measured[dropout_axis] = 0.0; measured_accel[dropout_axis] = 0.0

        output = messaging.IMUSensorMsgPayload()
        output.AngVelPlatform = measured
        output.AccelPlatform = measured_accel
        output.DRFramePlatform = [float(x) for x in source.DRFramePlatform]
        output.DVFramePlatform = [float(x) for x in source.DVFramePlatform]
        self.imuOutMsg.write(output, current_sim_nanos, self.moduleID)

        bias_payload = messaging.IMUSensorMsgPayload()
        bias_payload.AngVelPlatform = bias
        bias_payload.AccelPlatform = accel_bias
        self.biasOutMsg.write(bias_payload, current_sim_nanos, self.moduleID)
        noise_payload = messaging.IMUSensorMsgPayload()
        noise_payload.AngVelPlatform = noise
        noise_payload.AccelPlatform = accel_noise
        self.noiseOutMsg.write(noise_payload, current_sim_nanos, self.moduleID)


class ReactionWheelSpeedFaultInjector(sysModel.SysModel):
    """Inject tachometer bias without changing physical wheel speed."""

    EFFECTS = {"adcs_rw_speed_sensor_fault"}

    def __init__(self, events: Sequence[BSKEventSpec], *, wheel_count: int) -> None:
        super().__init__(); self.ModelTag = "project_rw_speed_fault_injector"
        self.events = tuple(event for event in events if event.effect in self.EFFECTS)
        self.wheel_count = int(wheel_count); self.speedInMsg = messaging.RWSpeedMsgReader(); self.speedOutMsg = messaging.RWSpeedMsg()

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        source = self.speedInMsg(); speeds = list(source.wheelSpeeds)
        for event in _active(self.events, current_sim_nanos):
            index = _wheel_index(event, self.wheel_count)
            speeds[index] += _event_number(event, ("speed_bias_rad_s",), 25.0)
        payload = messaging.RWSpeedMsgPayload(); payload.wheelSpeeds = speeds
        self.speedOutMsg.write(payload, current_sim_nanos, self.moduleID)


class ReactionWheelCommandFaultManager(sysModel.SysModel):
    """Modify wheel torque commands before they reach the wheel dynamics."""

    EFFECTS = {
        "rw_jamming", "adcs_rw_jamming", "rw_motor_failure", "adcs_rw_motor_failure",
        "rw_friction_degradation", "adcs_rw_torque_authority_loss", "reaction_wheel_speed_limit", "adcs_reaction_wheel_speed_limit",
        "adcs_actuator_failure", "adcs_control_loop_failure",
    }

    def __init__(self, events: Sequence[BSKEventSpec], *, wheel_count: int = 3, max_torque_nm: float = 0.2) -> None:
        super().__init__()
        self.ModelTag = "project_rw_command_fault_manager"
        self.events = tuple(event for event in events if event.effect in self.EFFECTS)
        self.wheel_count = int(wheel_count)
        self.max_torque_nm = abs(float(max_torque_nm))
        self.commandInMsg = messaging.ArrayMotorTorqueMsgReader()
        self.speedInMsg = messaging.RWSpeedMsgReader()
        self.commandOutMsg = messaging.ArrayMotorTorqueMsg()
        self.dragOutMsg = messaging.ArrayMotorTorqueMsg()
        self.torqueLimitOutMsg = messaging.ArrayMotorTorqueMsg()
        self.speedLimitOutMsg = messaging.RWSpeedMsg()

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        command_payload = self.commandInMsg()
        speed_payload = self.speedInMsg()
        source = list(command_payload.motorTorque)
        output = list(source)
        drag = [0.0] * len(output)
        torque_limits = [self.max_torque_nm] * len(output)
        speed_limits = [0.0] * len(list(speed_payload.wheelSpeeds))
        speeds = list(speed_payload.wheelSpeeds)

        for event in _active(self.events, current_sim_nanos):
            index = _wheel_index(event, self.wheel_count)
            if event.effect in {"rw_motor_failure", "adcs_rw_motor_failure"}:
                scale = max(0.0, min(_event_number(event, ("torque_scale", "remaining_torque_ratio"), 0.0), 1.0))
                output[index] *= scale
                torque_limits[index] = self.max_torque_nm * scale
            elif event.effect == "adcs_actuator_failure":
                output[index] = 0.0; torque_limits[index] = 0.0
            elif event.effect == "adcs_control_loop_failure":
                scale = max(0.0, min(_event_number(event, ("command_scale",), 0.0), 1.0))
                for wheel_index in range(self.wheel_count):
                    output[wheel_index] *= scale
            elif event.effect == "adcs_rw_torque_authority_loss":
                scale = max(0.0, min(_event_number(event, ("torque_scale", "remaining_torque_ratio"), 0.5), 1.0))
                output[index] *= scale
                torque_limits[index] = self.max_torque_nm * scale
            elif event.effect == "rw_friction_degradation":
                coefficient = max(_event_number(event, ("drag_nms",), 0.01), 0.0)
                drag[index] = coefficient
                output[index] -= coefficient * float(speeds[index])
            elif event.effect in {"reaction_wheel_speed_limit", "adcs_reaction_wheel_speed_limit"}:
                limit = max(_event_number(event, ("max_speed_rad_s",), 50.0), 0.0)
                speed_limits[index] = limit
                speed = float(speeds[index])
                if abs(speed) >= limit:
                    gain = max(_event_number(event, ("overspeed_brake_gain_nms",), 0.02), 0.0)
                    brake = min(self.max_torque_nm, gain * max(0.0, abs(speed) - limit))
                    output[index] = -math.copysign(brake, speed)
            elif event.effect in {"rw_jamming", "adcs_rw_jamming"}:
                brake = min(max(_event_number(event, ("brake_torque_nm",), self.max_torque_nm), 0.0), self.max_torque_nm)
                tolerance = max(_event_number(event, ("lock_tolerance_rad_s",), 0.25), 0.0)
                speed = float(speeds[index])
                output[index] = -math.copysign(brake, speed) if abs(speed) > tolerance else 0.0
                torque_limits[index] = 0.0

        for index in range(min(self.wheel_count, len(output))):
            output[index] = max(-self.max_torque_nm, min(self.max_torque_nm, float(output[index])))

        out_payload = messaging.ArrayMotorTorqueMsgPayload()
        out_payload.motorTorque = output
        self.commandOutMsg.write(out_payload, current_sim_nanos, self.moduleID)
        drag_payload = messaging.ArrayMotorTorqueMsgPayload()
        drag_payload.motorTorque = drag
        self.dragOutMsg.write(drag_payload, current_sim_nanos, self.moduleID)
        torque_payload = messaging.ArrayMotorTorqueMsgPayload()
        torque_payload.motorTorque = torque_limits
        self.torqueLimitOutMsg.write(torque_payload, current_sim_nanos, self.moduleID)
        speed_payload_out = messaging.RWSpeedMsgPayload()
        speed_payload_out.wheelSpeeds = speed_limits
        self.speedLimitOutMsg.write(speed_payload_out, current_sim_nanos, self.moduleID)


class PowerDataModeGate(sysModel.SysModel):
    """Runtime payload/downlink gate driven by battery, ADCS and events."""

    def __init__(
        self,
        *,
        min_soc: float = 0.20,
        max_pointing_error_deg: float = 20.0,
        comm_max_pointing_error_deg: float | None = None,
        events: Sequence[BSKEventSpec] = (),
    ) -> None:
        super().__init__()
        self.ModelTag = "project_power_data_mode_gate"
        self.batteryInMsg = messaging.PowerStorageStatusMsgReader()
        self.guidInMsg = messaging.AttGuidMsgReader()
        self.min_soc = float(min_soc)
        self.max_pointing_error_deg = float(max_pointing_error_deg)
        self.comm_max_pointing_error_deg = float(comm_max_pointing_error_deg if comm_max_pointing_error_deg is not None else max_pointing_error_deg)
        self.events = tuple(events)
        self.payloadCmdOutMsg = messaging.DeviceCmdMsg()
        self.downlinkCmdOutMsg = messaging.DeviceCmdMsg()
        self.payloadStatusOutMsg = messaging.DeviceStatusMsg()
        self.downlinkStatusOutMsg = messaging.DeviceStatusMsg()
        self.safeModeStatusOutMsg = messaging.DeviceStatusMsg()
        self.payloadPowerPermitInMsg = messaging.DeviceStatusMsgReader()
        self.commPowerPermitInMsg = messaging.DeviceStatusMsgReader()
        self.thermalStatusInMsg = messaging.DeviceStatusMsgReader()
        self._has_payload_power_permit = False
        self._has_comm_power_permit = False
        self._has_thermal_status = False

    def subscribe_power_permits(self, *, payload_status_msg: object | None = None, comm_status_msg: object | None = None) -> None:
        if payload_status_msg is not None:
            self.payloadPowerPermitInMsg.subscribeTo(payload_status_msg)
            self._has_payload_power_permit = True
        if comm_status_msg is not None:
            self.commPowerPermitInMsg.subscribeTo(comm_status_msg)
            self._has_comm_power_permit = True

    def subscribe_thermal_status(self, thermal_status_msg: object | None) -> None:
        if thermal_status_msg is not None:
            self.thermalStatusInMsg.subscribeTo(thermal_status_msg)
            self._has_thermal_status = True

    @staticmethod
    def _permit(reader: object, linked: bool) -> bool:
        if not linked:
            return True
        try:
            return bool(reader().deviceStatus)
        except Exception:
            return False

    @staticmethod
    def _pointing_error_deg(sigma_br: object) -> float:
        sigma_norm = math.sqrt(sum(float(x) ** 2 for x in list(sigma_br)[:3]))
        return 4.0 * math.degrees(math.atan(sigma_norm))

    @staticmethod
    def _write_command(msg: object, enabled: int, nanos: int, module_id: int) -> None:
        payload = messaging.DeviceCmdMsgPayload()
        payload.deviceCmd = int(enabled)
        msg.write(payload, nanos, module_id)

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        battery = self.batteryInMsg()
        guidance = self.guidInMsg()
        capacity = max(float(battery.storageCapacity), 1.0e-12)
        soc = float(battery.storageLevel) / capacity
        pointing_error_deg = self._pointing_error_deg(guidance.sigma_BR)
        active = _active(self.events, current_sim_nanos)
        threshold = self.min_soc
        for event in active:
            if event.effect == "power_safe_mode_threshold":
                threshold = max(threshold, _event_number(event, ("soc_threshold", "min_soc", "threshold"), self.min_soc))
        payload_forced_off = any(event.effect == "payload_instrument_off" for event in active)
        downlink_forced_off = any(event.effect == "comm_data_downlink_link_loss" for event in active)
        thermal_safe = self._permit(self.thermalStatusInMsg, self._has_thermal_status)
        safe_mode = int(soc < threshold or not thermal_safe)
        payload_power_permit = self._permit(self.payloadPowerPermitInMsg, self._has_payload_power_permit)
        comm_power_permit = self._permit(self.commPowerPermitInMsg, self._has_comm_power_permit)
        payload_enabled = int(not payload_forced_off and not safe_mode and payload_power_permit and pointing_error_deg <= self.max_pointing_error_deg)
        downlink_enabled = int(not downlink_forced_off and not safe_mode and comm_power_permit and pointing_error_deg <= self.comm_max_pointing_error_deg)
        self._write_command(self.payloadCmdOutMsg, payload_enabled, current_sim_nanos, self.moduleID)
        self._write_command(self.downlinkCmdOutMsg, downlink_enabled, current_sim_nanos, self.moduleID)
        _write_status(self.payloadStatusOutMsg, payload_enabled, current_sim_nanos, self.moduleID)
        _write_status(self.downlinkStatusOutMsg, downlink_enabled, current_sim_nanos, self.moduleID)
        _write_status(self.safeModeStatusOutMsg, safe_mode, current_sim_nanos, self.moduleID)


class NativeRfDownlinkGate(sysModel.SysModel):
    """Gate a native LinkBudget message by access, pointing, thermal and PDU permits.

    The module never creates or raises CNR. It only passes the native RF payload
    through when every mission condition is true; otherwise it publishes an OFF
    link with zero CNR so DownlinkHandling cannot drain storage.
    """

    def __init__(self, *, max_pointing_error_deg: float = 20.0) -> None:
        super().__init__()
        self.ModelTag = "project_native_rf_downlink_gate"
        self.max_pointing_error_deg = float(max_pointing_error_deg)
        self.accessInMsg = messaging.AccessMsgReader()
        self.linkBudgetInMsg = messaging.LinkBudgetMsgReader()
        self.guidInMsg = messaging.AttGuidMsgReader()
        self.commandPermitInMsg = messaging.DeviceStatusMsgReader()
        self.thermalStatusInMsg = messaging.DeviceStatusMsgReader()
        self.linkBudgetOutMsg = messaging.LinkBudgetMsg()
        self.trace: list[dict[str, Any]] = []
        self.read_errors: list[str] = []

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.trace.clear()
        self.read_errors.clear()
        self.UpdateState(current_sim_nanos)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        try:
            access = self.accessInMsg()
            native = self.linkBudgetInMsg()
            guidance = self.guidInMsg()
            permit = bool(self.commandPermitInMsg().deviceStatus)
            thermal_safe = bool(self.thermalStatusInMsg().deviceStatus)
        except Exception as exc:
            if int(current_sim_nanos) > 0:
                self.read_errors.append(f"{type(exc).__name__}: {exc}")
            access = None; native = None; guidance = None; permit = False; thermal_safe = False
        pointing_error_deg = PowerDataModeGate._pointing_error_deg(getattr(guidance, "sigma_BR", [0.0, 0.0, 0.0])) if guidance is not None else float("inf")
        geometric_access = bool(int(getattr(access, "hasAccess", 0))) if access is not None else False
        active = bool(geometric_access and permit and thermal_safe and pointing_error_deg <= self.max_pointing_error_deg and native is not None)
        out = messaging.LinkBudgetMsgPayload()
        if native is not None:
            out.antennaName1 = str(getattr(native, "antennaName1", "spacecraft") or "spacecraft")
            out.antennaName2 = str(getattr(native, "antennaName2", "ground") or "ground")
            out.distance = max(0.0, float(getattr(native, "distance", 0.0)))
            out.bandwidth = max(0.0, float(getattr(native, "bandwidth", 0.0)))
            out.frequency = max(0.0, float(getattr(native, "frequency", 0.0)))
        if active:
            out.antennaState1 = int(getattr(native, "antennaState1", 2))
            out.antennaState2 = int(getattr(native, "antennaState2", 1))
            out.CNR1 = max(0.0, float(getattr(native, "CNR1", 0.0)))
            out.CNR2 = max(0.0, float(getattr(native, "CNR2", 0.0)))
        else:
            out.antennaState1 = 0
            out.antennaState2 = 0
            out.CNR1 = 0.0
            out.CNR2 = 0.0
        self.linkBudgetOutMsg.write(out, current_sim_nanos, self.moduleID)
        self.trace.append({
            "time_s": _seconds(current_sim_nanos),
            "geometric_access": geometric_access,
            "pointing_error_deg": pointing_error_deg,
            "command_permit": permit,
            "thermal_safe": thermal_safe,
            "link_active": active,
            "native_cnr": max(float(getattr(native, "CNR1", 0.0)), float(getattr(native, "CNR2", 0.0))) if native is not None else 0.0,
        })


class PropulsionBurnController(sysModel.SysModel):
    """Issue one in-task burn command subject to battery/safe-mode permission.

    The module owns only mission burn scheduling and permission logic. Thrust,
    mass flow and spacecraft response remain in official Basilisk modules.
    """

    def __init__(
        self,
        *,
        burn_start_s: float,
        on_time_s: Sequence[float],
        min_soc: float = 0.30,
        events: Sequence[BSKEventSpec] = (),
    ) -> None:
        super().__init__()
        self.ModelTag = "project_propulsion_burn_controller"
        self.burn_start_s = max(float(burn_start_s), 0.0)
        self.on_time_s = tuple(max(float(value), 0.0) for value in on_time_s)
        self.min_soc = max(0.0, min(float(min_soc), 1.0))
        self.events = tuple(
            event
            for event in events
            if event.effect in {
                "propulsion_thruster_ignition_failure",
                "propulsion_burn_impulse_loss",
            }
        )
        self.batteryInMsg = messaging.PowerStorageStatusMsgReader()
        self.safeModeInMsg = messaging.DeviceStatusMsgReader()
        self.commandOutMsg = messaging.THRArrayOnTimeCmdMsg()
        self.burnStatusOutMsg = messaging.DeviceStatusMsg()
        self.permissionStatusOutMsg = messaging.DeviceStatusMsg()
        self._issued = False
        self._issued_at_s: float | None = None
        self._issued_duration_s = 0.0

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self._issued = False
        self._issued_at_s = None
        self._issued_duration_s = 0.0
        payload = messaging.THRArrayOnTimeCmdMsgPayload()
        payload.OnTimeRequest = [0.0] * len(payload.OnTimeRequest)
        self.commandOutMsg.write(payload, current_sim_nanos, self.moduleID)
        _write_status(self.burnStatusOutMsg, 0, current_sim_nanos, self.moduleID)
        _write_status(self.permissionStatusOutMsg, 0, current_sim_nanos, self.moduleID)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        now_s = _seconds(current_sim_nanos)
        battery = self.batteryInMsg()
        safe_mode = self.safeModeInMsg()
        capacity = max(float(battery.storageCapacity), 1.0e-12)
        soc = float(battery.storageLevel) / capacity
        permitted = int(soc >= self.min_soc and int(safe_mode.deviceStatus) == 0)
        _write_status(self.permissionStatusOutMsg, permitted, current_sim_nanos, self.moduleID)
        if not self._issued and now_s + 1.0e-12 >= self.burn_start_s and permitted:
            impulse_ratio = 1.0
            for event in _active(self.events, current_sim_nanos):
                if event.effect == "propulsion_thruster_ignition_failure":
                    impulse_ratio = 0.0
                elif event.effect == "propulsion_burn_impulse_loss":
                    impulse_ratio = min(
                        impulse_ratio,
                        max(
                            0.0,
                            min(
                                1.0,
                                _event_number(
                                    event,
                                    ("remaining_impulse_ratio", "remaining_ratio"),
                                    0.5,
                                ),
                            ),
                        ),
                    )
            payload = messaging.THRArrayOnTimeCmdMsgPayload()
            values = list(payload.OnTimeRequest)
            for index, value in enumerate(self.on_time_s[: len(values)]):
                values[index] = value * impulse_ratio
            payload.OnTimeRequest = values
            self.commandOutMsg.write(payload, current_sim_nanos, self.moduleID)
            self._issued = True
            self._issued_at_s = now_s
            self._issued_duration_s = max(
                (float(value) for value in values[: len(self.on_time_s)]),
                default=0.0,
            )
        active = 0
        if self._issued_at_s is not None:
            active = int(
                self._issued_duration_s > 0.0
                and now_s <= self._issued_at_s + self._issued_duration_s + 1.0e-12
            )
        _write_status(self.burnStatusOutMsg, active, current_sim_nanos, self.moduleID)


class WholeSpacecraftParameterEventController(sysModel.SysModel):
    """Apply parameter faults/degradations before official module updates."""

    EFFECTS = {"eps_battery_capacity_loss", "solar_panel_efficiency_loss", "thermal_radiator_rejection_loss"}

    def __init__(
        self,
        events: Sequence[BSKEventSpec],
        *,
        battery: Any,
        solar_panel: Any,
        thermal: Any,
    ) -> None:
        super().__init__()
        self.ModelTag = "project_whole_parameter_event_controller"
        self.events = tuple(event for event in events if event.effect in self.EFFECTS)
        self.battery = battery
        self.solar_panel = solar_panel
        self.thermal = thermal
        self.nominal_capacity = float(battery.storageCapacity)
        self.nominal_efficiency = float(solar_panel.panelEfficiency)
        self.nominal_emissivity = float(getattr(thermal, "sensorEmissivity", 0.0))
        self.nominal_radiator_factors = {
            str(name): float(getattr(radiator, "rejection_factor", 1.0))
            for name, radiator in getattr(thermal, "radiators", {}).items()
        }

    def Reset(self, current_sim_nanos: int) -> None:  # noqa: N802
        self.UpdateState(current_sim_nanos)

    @staticmethod
    def _remaining_ratio(event: BSKEventSpec, default: float = 0.5) -> float:
        if "remaining_capacity_ratio" in event.parameters:
            value = event.parameters.get("remaining_capacity_ratio")
        elif "remaining_efficiency_ratio" in event.parameters:
            value = event.parameters.get("remaining_efficiency_ratio")
        elif "remaining_rejection_ratio" in event.parameters:
            value = event.parameters.get("remaining_rejection_ratio")
        elif "remaining_ratio" in event.parameters:
            value = event.parameters.get("remaining_ratio")
        elif "loss_fraction" in event.parameters:
            value = 1.0 - float(event.parameters.get("loss_fraction", 0.0))
        elif "loss_pct" in event.parameters:
            value = 1.0 - float(event.parameters.get("loss_pct", 0.0)) / 100.0
        else:
            value = default
        try:
            return max(0.0, min(float(value), 1.0))
        except (TypeError, ValueError):
            return float(default)

    def UpdateState(self, current_sim_nanos: int) -> None:  # noqa: N802
        capacity_ratio = 1.0
        efficiency_ratio = 1.0
        rejection_ratio = 1.0
        for event in _active(self.events, current_sim_nanos):
            if event.effect == "eps_battery_capacity_loss":
                capacity_ratio = min(capacity_ratio, self._remaining_ratio(event, 0.5))
            elif event.effect == "solar_panel_efficiency_loss":
                efficiency_ratio = min(efficiency_ratio, self._remaining_ratio(event, 0.5))
            elif event.effect == "thermal_radiator_rejection_loss":
                rejection_ratio = min(rejection_ratio, self._remaining_ratio(event, 0.5))
        self.battery.storageCapacity = self.nominal_capacity * capacity_ratio
        self.solar_panel.panelEfficiency = self.nominal_efficiency * efficiency_ratio
        if self.nominal_radiator_factors:
            for name, nominal in self.nominal_radiator_factors.items():
                radiator = self.thermal.radiators.get(name)
                if radiator is not None:
                    radiator.rejection_factor = nominal * rejection_ratio
        elif hasattr(self.thermal, "sensorEmissivity"):
            self.thermal.sensorEmissivity = self.nominal_emissivity * rejection_ratio


__all__ = [
    "AdcsSensorFusion",
    "EarthHorizonSensor",
    "StarTrackerFaultInjector",
    "SunDirectionSensor",
    "SunVectorFaultInjector",
    "ImuFaultInjector",
    "NativeEventStatus",
    "NativeRfDownlinkGate",
    "PowerDataModeGate",
    "PropulsionBurnController",
    "ReactionWheelCommandFaultManager",
    "ReactionWheelSpeedFaultInjector",
    "WholeSpacecraftParameterEventController",
]
