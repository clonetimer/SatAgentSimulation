"""FSW declarations for the native Basilisk foundation."""
from __future__ import annotations

from .master import SatelliteBSKSim
from .types import BSKConnectionSpec, BSKModuleSpec


class SatelliteFswModel:
    process_name = "FswProcess"
    task_name = "FswTask"

    def __init__(self, step_s: float, mode_request: str = "standby") -> None:
        self.step_s = float(step_s)
        self.mode_request = mode_request

    def attach(self, sim: SatelliteBSKSim) -> None:
        sim.create_process(self.process_name, role="fsw", description="Basilisk flight-software process")
        sim.create_task(self.task_name, process=self.process_name, rate_s=self.step_s, description="Flight software task")

    def modules(self) -> tuple[BSKModuleSpec, ...]:
        return (
            BSKModuleSpec("mode_request", "project.modeRequest", self.task_name, "mode control surface", status="project_control_surface"),
            BSKModuleSpec("inertial_guidance", "Basilisk.fswAlgorithms.inertial3D", self.task_name, "inertial guidance", source="Basilisk", status="instantiated_native_module"),
            BSKModuleSpec("attitude_error", "Basilisk.fswAlgorithms.attTrackingError", self.task_name, "tracking error", source="Basilisk", status="instantiated_native_module"),
            BSKModuleSpec("mrp_feedback", "Basilisk.fswAlgorithms.mrpFeedback", self.task_name, "attitude controller", source="Basilisk", status="instantiated_native_module"),
            BSKModuleSpec("rw_motor_torque", "Basilisk.fswAlgorithms.rwMotorTorque", self.task_name, "body torque to wheel torque allocation", source="Basilisk", status="instantiated_native_module"),
        )

    def connections(self) -> tuple[BSKConnectionSpec, ...]:
        return (
            BSKConnectionSpec("simple_nav.attOutMsg", "attitude_error.attNavInMsg", "导航姿态进入姿态误差模块"),
            BSKConnectionSpec("inertial_guidance.attRefOutMsg", "attitude_error.attRefInMsg", "制导参考进入姿态误差模块"),
            BSKConnectionSpec("attitude_error.attGuidOutMsg", "mrp_feedback.guidInMsg", "姿态误差进入控制器"),
            BSKConnectionSpec("vehicle_config", "mrp_feedback.vehConfigInMsg", "航天器惯量进入控制器"),
            BSKConnectionSpec("rw_config", "mrp_feedback.rwParamsInMsg", "反作用轮构型进入控制器"),
            BSKConnectionSpec("reaction_wheels.rwSpeedOutMsg", "mrp_feedback.rwSpeedsInMsg", "轮速进入控制器"),
            BSKConnectionSpec("mrp_feedback.cmdTorqueOutMsg", "rw_motor_torque.vehControlInMsg", "控制力矩进入轮力矩分配"),
        )
