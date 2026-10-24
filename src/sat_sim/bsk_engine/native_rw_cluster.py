"""Genuine three/four-wheel native Basilisk cluster, not an equation surrogate.

Only nominal, fixed motor torque commands are supported. The same orthogonal_3
and pyramid_4 axes as the whole-spacecraft native graph are used. This does
NOT establish whole-spacecraft trajectory parity or production fault support.
"""
from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Sequence

from sat_sim.bsk_engine.native_rw_component import _real
from sat_sim.bsk_engine.rw_geometry import standard_rw_axes


@dataclass(frozen=True)
class NativeRWClusterTrace:
    times_s: tuple[float, ...]
    axes_b: tuple[tuple[float, float, float], ...]
    wheel_speed_rad_s: tuple[tuple[float, ...], ...]  # time × wheel
    applied_motor_torque_nm: tuple[tuple[float, ...], ...]
    measured_friction_torque_nm: tuple[tuple[float, ...], ...]
    factory_js_kg_m2: tuple[float, ...]
    factory_u_max_nm: tuple[float, ...]
    factory_omega_max_rad_s: tuple[float, ...]
    viscous_damping_nms: tuple[float, ...]
    model_source: str = "Basilisk.simulation.reactionWheelStateEffector"

    def as_dict(self) -> dict:
        return {name: [list(v) if isinstance(v, tuple) else v for v in getattr(self, name)]
                for name in ("times_s", "axes_b", "wheel_speed_rad_s",
                             "applied_motor_torque_nm", "measured_friction_torque_nm",
                             "factory_js_kg_m2", "factory_u_max_nm",
                             "factory_omega_max_rad_s", "viscous_damping_nms")} | {
                                 "model_source": self.model_source}


def _wheel_values(value: float | Sequence[float], name: str, count: int,
                  *, lower: float, upper: float, strict_positive: bool = False) -> tuple[float, ...]:
    if isinstance(value, (list, tuple)):
        if len(value) != count:
            raise ValueError("NATIVE_RW_VECTOR_LENGTH_INVALID:" + name)
        items = value
    else:
        items = (value,) * count
    return tuple(_real(x, name, lower=lower, upper=upper, strict_positive=strict_positive)
                 for x in items)


def run_native_rw_cluster(
    *,
    configuration: str,
    duration_s: float = 3.0,
    step_s: float = .1,
    inertia_kg_m2: float | Sequence[float] = .1,
    initial_speed_rad_s: float | Sequence[float] = 0.,
    command_torque_nm: float | Sequence[float] = .01,
    max_motor_torque_nm: float | Sequence[float] = .02,
    max_speed_rad_s: float | Sequence[float] = 1000.,
    damping_nms: float | Sequence[float] = 1e-5,
) -> NativeRWClusterTrace:
    """Return unmodified native SWIG speed, motor torque and friction records."""
    axes = standard_rw_axes(configuration)
    count = len(axes)
    duration = _real(duration_s, "duration_s", lower=.01, upper=120., strict_positive=True)
    step = _real(step_s, "step_s", lower=.001, upper=1., strict_positive=True)
    if step > duration or duration / step > 20000:
        raise ValueError("NATIVE_RW_STEP_DURATION_INVALID")
    inertia = _wheel_values(inertia_kg_m2, "inertia_kg_m2", count, lower=1e-6, upper=10., strict_positive=True)
    initial = _wheel_values(initial_speed_rad_s, "initial_speed_rad_s", count, lower=-5000., upper=5000.)
    requested = _wheel_values(command_torque_nm, "command_torque_nm", count, lower=-1., upper=1.)
    limits = _wheel_values(max_motor_torque_nm, "max_motor_torque_nm", count,
                           lower=1e-7, upper=.2, strict_positive=True)
    speed_max = _wheel_values(max_speed_rad_s, "max_speed_rad_s", count,
                              lower=1., upper=10000., strict_positive=True)
    damping = _wheel_values(damping_nms, "damping_nms", count, lower=0., upper=.01)
    if any(abs(a) >= b for a, b in zip(initial, speed_max)):
        raise ValueError("NATIVE_RW_INITIAL_SPEED_LIMIT_INVALID")

    # Genuine C++ Basilisk models; no Python physics fallback.
    from Basilisk.architecture import messaging
    from Basilisk.simulation import reactionWheelStateEffector, spacecraft
    from Basilisk.utilities import SimulationBaseClass, macros, simIncludeRW

    sim = SimulationBaseClass.SimBaseClass()
    process = sim.CreateNewProcess("NativeRWClusterProcess")
    process.addTask(sim.CreateNewTask("NativeRWClusterTask", macros.sec2nano(step)))
    hub = spacecraft.Spacecraft()
    hub.ModelTag = "NativeRWClusterHub"
    hub.hub.mHub = 100.
    hub.hub.IHubPntBc_B = [[100., 0., 0.], [0., 80., 0.], [0., 0., 60.]]
    hub.hub.omega_BN_BInit = [0., 0., 0.]
    wheels = reactionWheelStateEffector.ReactionWheelStateEffector()
    wheels.ModelTag = "NativeRWClusterEffector"
    factory = simIncludeRW.rwFactory()
    devices = []
    for i, axis in enumerate(axes):
        devices.append(factory.create(
            "custom", list(axis), Js=inertia[i], Omega=initial[i] / macros.RPM,
            Omega_max=speed_max[i] / macros.RPM,
            u_max=limits[i], useMaxTorque=True, useMinTorque=False,
            useRWfriction=damping[i] > 0., cViscous=damping[i],
            label="RW" + str(i + 1), rWB_B=[0., 0., 0.],
        ))
    factory.addToSpacecraft(wheels.ModelTag, wheels, hub)
    sim.AddModelToTask("NativeRWClusterTask", wheels, None, 110)
    sim.AddModelToTask("NativeRWClusterTask", hub, None, 100)

    payload = messaging.ArrayMotorTorqueMsgPayload()
    payload.motorTorque = [*requested, *([0.] * (36 - count))]
    cmd = messaging.ArrayMotorTorqueMsg().write(payload)
    wheels.rwMotorCmdInMsg.subscribeTo(cmd)

    speed_rec = wheels.rwSpeedOutMsg.recorder(macros.sec2nano(step))
    sim.AddModelToTask("NativeRWClusterTask", speed_rec, None, 10)
    logs = []
    for i in range(count):
        log = wheels.rwOutMsgs[i].recorder(macros.sec2nano(step))
        sim.AddModelToTask("NativeRWClusterTask", log, None, 9 - i)
        logs.append(log)
    sim.InitializeSimulation()
    sim.ConfigureStopTime(macros.sec2nano(duration))
    sim.ExecuteSimulation()

    times = tuple(float(ns) * 1e-9 for ns in speed_rec.times())
    speeds = tuple(tuple(float(x) for x in row[:count]) for row in speed_rec.wheelSpeeds)
    applied_columns = [tuple(float(x) for x in rec.u_current) for rec in logs]
    friction_columns = [tuple(float(x) for x in rec.frictionTorque) for rec in logs]
    if (not times or len(times) != len(speeds)
            or any(tuple(float(t) * 1e-9 for t in rec.times()) != times for rec in logs)
            or any(len(v) != len(times) for v in (*applied_columns, *friction_columns))
            or any(len(v) != count for v in speeds)):
        raise RuntimeError("NATIVE_RW_CLUSTER_RECORDER_INCONSISTENT")
    applied = tuple(tuple(column[t] for column in applied_columns) for t in range(len(times)))
    friction = tuple(tuple(column[t] for column in friction_columns) for t in range(len(times)))
    if not all(isfinite(x) for matrix in (speeds, applied, friction) for row in matrix for x in row):
        raise RuntimeError("NATIVE_RW_CLUSTER_NONFINITE_TELEMETRY")
    return NativeRWClusterTrace(
        times_s=times, axes_b=axes, wheel_speed_rad_s=speeds,
        applied_motor_torque_nm=applied, measured_friction_torque_nm=friction,
        factory_js_kg_m2=tuple(float(x.Js) for x in devices),
        factory_u_max_nm=tuple(float(x.u_max) for x in devices),
        factory_omega_max_rad_s=tuple(float(x.Omega_max) for x in devices),
        viscous_damping_nms=damping,
    )
