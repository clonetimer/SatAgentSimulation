"""Dynamics declarations for the native Basilisk foundation."""
from __future__ import annotations

from .master import SatelliteBSKSim
from .types import BSKConnectionSpec, BSKModuleSpec


class SatelliteDynamicsModel:
    process_name = "DynamicsProcess"
    task_name = "DynamicsTask"

    def __init__(self, step_s: float) -> None:
        self.step_s = float(step_s)

    def attach(self, sim: SatelliteBSKSim) -> None:
        sim.create_process(self.process_name, role="dynamics", description="Basilisk dynamics process")
        sim.create_task(self.task_name, process=self.process_name, rate_s=self.step_s, description="Dynamics integration task")

    def modules(self) -> tuple[BSKModuleSpec, ...]:
        return (
            BSKModuleSpec("spacecraft", "Basilisk.simulation.spacecraft.Spacecraft", self.task_name, "spacecraft hub", source="Basilisk", status="instantiated_native_module"),
            BSKModuleSpec("earth_gravity", "Basilisk.utilities.simIncludeGravBody", self.task_name, "central Earth gravity", source="Basilisk", status="instantiated_native_environment"),
            BSKModuleSpec("reaction_wheels", "Basilisk.simulation.reactionWheelStateEffector", self.task_name, "reaction-wheel state effector", source="Basilisk", status="instantiated_native_module"),
            BSKModuleSpec("simple_nav", "Basilisk.simulation.simpleNav.SimpleNav", self.task_name, "navigation truth interface", source="Basilisk", status="instantiated_native_module"),
        )

    def connections(self) -> tuple[BSKConnectionSpec, ...]:
        return (
            BSKConnectionSpec("spacecraft.scStateOutMsg", "simple_nav.scStateInMsg", "航天器状态进入导航模块"),
            BSKConnectionSpec("rw_motor_torque.rwMotorTorqueOutMsg", "reaction_wheels.rwMotorCmdInMsg", "电机力矩命令进入反作用轮动力学"),
        )
