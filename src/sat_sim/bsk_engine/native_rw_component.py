"""Isolated, *executed* official Basilisk RW component physics probe.

Research gate only: not yet a selectable TaskSpec capability. The native wheel
is physically mounted on a Basilisk spacecraft hub so rotor/hub momentum
exchange is real. No project-owned wheel dynamics or synthetic trace is used.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class NativeWheelTrace:
    times_s: tuple[float, ...]
    wheel_speed_rad_s: tuple[float, ...]
    applied_motor_torque_nm: tuple[float, ...]
    measured_friction_torque_nm: tuple[float, ...]
    viscous_damping_nms: float
    factory_js_kg_m2: float
    factory_u_max_nm: float
    factory_omega_max_rad_s: float
    model_source: str = "Basilisk.simulation.reactionWheelStateEffector"

    def as_dict(self) -> dict:
        return {
            "times_s": list(self.times_s),
            "wheel_speed_rad_s": list(self.wheel_speed_rad_s),
            "applied_motor_torque_nm": list(self.applied_motor_torque_nm),
            "measured_friction_torque_nm": list(self.measured_friction_torque_nm),
            "viscous_damping_nms": self.viscous_damping_nms,
            "factory_js_kg_m2": self.factory_js_kg_m2,
            "factory_u_max_nm": self.factory_u_max_nm,
            "factory_omega_max_rad_s": self.factory_omega_max_rad_s,
            "model_source": self.model_source,
        }


def _real(value: float, name: str, *, lower: float, upper: float, strict_positive: bool = False) -> float:
    if type(value) not in (int, float):
        raise ValueError(name + "_INVALID")
    x = float(value)
    if not math.isfinite(x) or x < lower or x > upper or (strict_positive and x == 0):
        raise ValueError(name + "_INVALID")
    return x


def run_native_rw_probe(
    *,
    duration_s: float = 3.0,
    step_s: float = 0.1,
    inertia_kg_m2: float = 0.1,
    initial_speed_rad_s: float = 0.0,
    command_torque_nm: float = 0.01,
    max_motor_torque_nm: float = 0.02,
    max_speed_rad_s: float = 1000.0,
    damping_nms: float = 0.0,
) -> NativeWheelTrace:
    """Run actual native SWIG RW + hub, and return the Basilisk recorder output.

    This intentionally excludes fault/degradation semantics, multi-wheel
    geometry, FSW, EPS, and cross-level equivalence. It is NOT the production
    component adapter. No fallback to Python physics is permitted.
    """
    duration = _real(duration_s, "duration_s", lower=0.01, upper=120., strict_positive=True)
    step = _real(step_s, "step_s", lower=0.001, upper=1., strict_positive=True)
    if step > duration or duration / step > 20000:
        raise ValueError("NATIVE_RW_STEP_DURATION_INVALID")
    inertia = _real(inertia_kg_m2, "inertia_kg_m2", lower=1e-6, upper=10., strict_positive=True)
    initial = _real(initial_speed_rad_s, "initial_speed_rad_s", lower=-5000., upper=5000.)
    command = _real(command_torque_nm, "command_torque_nm", lower=-1., upper=1.)
    torque_limit = _real(max_motor_torque_nm, "max_motor_torque_nm", lower=1e-7, upper=0.2, strict_positive=True)
    speed_limit = _real(max_speed_rad_s, "max_speed_rad_s", lower=1., upper=10000., strict_positive=True)
    damping = _real(damping_nms, "damping_nms", lower=0., upper=.01)
    if abs(initial) >= speed_limit:
        raise ValueError("NATIVE_RW_INITIAL_SPEED_LIMIT_INVALID")

    # Only import the real native binding AFTER validation. Missing Basilisk is
    # a hard error; do not manufacture a passing pseudo-Basilisk trace.
    from Basilisk.architecture import messaging
    from Basilisk.simulation import reactionWheelStateEffector, spacecraft
    from Basilisk.utilities import SimulationBaseClass, macros, simIncludeRW

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("ComponentRWNativeProcess")
    process.addTask(sim.CreateNewTask("ComponentRWNativeTask", macros.sec2nano(step)))

    hub = spacecraft.Spacecraft()
    hub.ModelTag = "ComponentRWPhysicalHub"
    hub.hub.mHub = 100.0
    hub.hub.IHubPntBc_B = [[100., 0., 0.], [0., 80., 0.], [0., 0., 60.]]
    hub.hub.omega_BN_BInit = [0., 0., 0.]

    wheels = reactionWheelStateEffector.ReactionWheelStateEffector()
    wheels.ModelTag = "ComponentRWNativeEffector"
    factory = simIncludeRW.rwFactory()
    # 'custom' preserves user Js directly. Omega/Omega_max are RPM in the
    # official factory; native telemetry is rad/s. Factory hardware u_max
    # saturation must be enabled, not merely clipped in Python.
    device = factory.create(
        "custom", [1., 0., 0.], Js=float(inertia),
        Omega=float(initial / macros.RPM),
        Omega_max=float(speed_limit / macros.RPM),
        u_max=float(torque_limit), useMaxTorque=True,
        useMinTorque=False, useRWfriction=(damping > 0.),
        cViscous=float(damping), label="RW1",
        rWB_B=[0., 0., 0.],
    )
    factory.addToSpacecraft(wheels.ModelTag, wheels, hub)
    sim.AddModelToTask("ComponentRWNativeTask", wheels, None, 110)
    sim.AddModelToTask("ComponentRWNativeTask", hub, None, 100)

    cmd_payload = messaging.ArrayMotorTorqueMsgPayload()
    cmd_payload.motorTorque = [float(command)] + [0.] * 35
    cmd_msg = messaging.ArrayMotorTorqueMsg().write(cmd_payload)
    wheels.rwMotorCmdInMsg.subscribeTo(cmd_msg)
    recorder = wheels.rwSpeedOutMsg.recorder(macros.sec2nano(step))
    sim.AddModelToTask("ComponentRWNativeTask", recorder, None, 5)
    # SWIG RWConfigLogMsg provides the *physically applied* torque (u_current).
    # Never calculate this field from Python-only clipping assumptions.
    torque_recorder = wheels.rwOutMsgs[0].recorder(macros.sec2nano(step))
    sim.AddModelToTask("ComponentRWNativeTask", torque_recorder, None, 4)

    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    measured_times = tuple(float(ns) * 1e-9 for ns in recorder.times())
    # recorder output is from the SWIG C++ RWSpeedMsg, not a Python equation.
    measured_speed = tuple(float(sample[0]) for sample in recorder.wheelSpeeds)
    measured_torque = tuple(float(v) for v in torque_recorder.u_current)
    measured_friction = tuple(float(v) for v in torque_recorder.frictionTorque)
    if (not measured_times or len(measured_times) != len(measured_speed)
            or len(measured_speed) != len(measured_torque)
            or len(measured_torque) != len(measured_friction)):
        raise RuntimeError("NATIVE_RW_REAL_RECORDER_MISSING")
    if not all(math.isfinite(x) for x in (*measured_speed, *measured_torque, *measured_friction)):
        raise RuntimeError("NATIVE_RW_NONFINITE_SPEED")
    return NativeWheelTrace(
        times_s=measured_times,
        wheel_speed_rad_s=measured_speed,
        applied_motor_torque_nm=measured_torque,
        measured_friction_torque_nm=measured_friction,
        viscous_damping_nms=damping,
        factory_js_kg_m2=float(device.Js),
        factory_u_max_nm=float(device.u_max),
        factory_omega_max_rad_s=float(device.Omega_max),
    )
