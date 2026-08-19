"""Chinese, executable scenario templates derived from Capability Registry defaults."""
from __future__ import annotations

import copy
from typing import Any, Mapping

from .form_schema import capability_form_schema
from .capability_registry import get_capability
from .agent_guards import evaluate_agent_guards
from .execution_planner import plan_task_spec
from .task_models import canonicalize_task_spec
from .task_validator import validate_task_spec
from .unified_agent import normalize_form_task_spec

SCENARIO_TEMPLATE_SCHEMA_VERSION = "scenario-templates.v4"


_SUBSYSTEM_OBJECT_BY_TARGET = {
    "adcs": "subsystem.adcs",
    "eps": "subsystem.eps",
    "thermal": "subsystem.thermal",
    "comm_data": "subsystem.comm_data",
    "propulsion": "subsystem.propulsion",
    "payload": "subsystem.payload",
}


def _default_object_id(capability_id: str) -> str:
    if capability_id.startswith("component."):
        return ".".join(capability_id.split(".")[:2])
    if capability_id.startswith("whole_spacecraft."):
        return "whole_spacecraft"
    if capability_id.startswith("orbit_environment."):
        return "orbit_environment"
    if capability_id.startswith("subsystem."):
        subsystem_prefixes = (
            ("subsystem.adcs", "subsystem.adcs"),
            ("subsystem.eps", "subsystem.eps"),
            ("subsystem.thermal", "subsystem.thermal"),
            ("subsystem.comm", "subsystem.comm_data"),
            ("subsystem.propulsion", "subsystem.propulsion"),
            ("subsystem.payload", "subsystem.payload"),
        )
        for prefix, object_id in subsystem_prefixes:
            if capability_id.startswith(prefix):
                return object_id
        target = capability_id.split(".")[1]
        return _SUBSYSTEM_OBJECT_BY_TARGET.get(target, f"subsystem.{target}")
    return capability_id


def _template(
    template_id: str,
    name: str,
    category: str,
    level: str,
    capability_id: str,
    summary: str,
    *,
    duration_s: float = 60.0,
    patch: Mapping[str, Any] | None = None,
    assertions: list[dict[str, Any]] | None = None,
    recommended: bool = False,
    object_id: str | None = None,
    execution_scope: str = "direct",
    expected_validation: str = "PASS",
    expected_mission_status: str | None = None,
    expected_validation_reason_code: str | None = None,
) -> dict[str, Any]:
    return {
        "template_id": template_id,
        "name": name,
        "category": category,
        "level": level,
        "capability_id": capability_id,
        "object_id": object_id or _default_object_id(capability_id),
        "execution_scope": execution_scope,
        "summary": summary,
        "duration_s": duration_s,
        "patch": copy.deepcopy(dict(patch or {})),
        "assertions": copy.deepcopy(assertions or []),
        "recommended": recommended,
        "runtime_test_required": True,
        "expected_validation": str(expected_validation),
        "expected_mission_status": expected_mission_status,
        "expected_validation_reason_code": expected_validation_reason_code,
    }


_TEMPLATES = [
    # Component-level templates
    _template(
        "component_reaction_wheel_nominal", "反作用轮加速", "部件", "component",
        "component.reaction_wheel.v1", "单个反作用轮在恒定正力矩作用下加速，输出转速、力矩和角动量。",
        patch={"parameters": {"values": {"num_wheels": 1, "initial_wheel_speeds_rad_s": 100.0, "command_torque_nm": 0.01}}},
        recommended=True,
    ),
    _template(
        "component_battery_cycle", "电池充放电", "部件", "component",
        "component.battery.v1", "电池在给定充放电功率下的荷电状态变化。",
    ),
    _template(
        "component_solar_panel_generation", "太阳帆板发电", "部件", "component",
        "component.solar_panel.v1", "太阳入射、阴影系数和帆板参数共同决定的发电功率。",
    ),
    _template(
        "component_thermal_node_response", "热节点温度响应", "部件", "component",
        "component.thermal_node.v1", "单热节点在内部功率和环境温度作用下的温度响应。",
    ),
    _template(
        "component_data_queue", "数据队列积压与下行", "部件", "component",
        "component.data_queue.v1", "数据生成速率高于下行速率时的队列积压过程。",
        patch={"parameters": {"values": {"generated_bps": 1500.0, "downlink_bps": 800.0}}},
    ),
    _template(
        "component_payload_sensor", "载荷传感器观测质量", "部件", "component",
        "component.payload_sensor.v1", "指向误差和云量对载荷观测质量及数据率的影响。",
    ),

    # Orbit/environment template
    _template(
        "orbit_leo_nominal", "近地轨道传播", "轨道环境", "orbit_environment",
        "orbit_environment.orbit_fidelity.v1", "500 km 近地轨道的轨道状态和环境量传播。",
        duration_s=600.0,
        patch={"parameters": {"values": {"altitude_m": 500000.0, "eccentricity": 0.0, "inclination_deg": 51.6}}},
        recommended=True,
    ),
    _template(
        "bsksim_foundation_orbit_attitude", "BSKSim基础轨道姿态场景", "BSKSim内核", "whole_spacecraft",
        "whole_spacecraft.bsksim_foundation.v1", "建立项目自有 BSKSim-style 场景—动力学—飞控分层骨架，并运行基础 Basilisk 时间线。",
        duration_s=60.0,
        patch={"simulation": {"sample_s": 5.0, "step_s": 1.0, "backend": "basilisk"}, "assurance": {"allow_proxy": False}, "parameters": {"values": {"initial_pointing_error_deg": 5.0, "orbit_rate_rad_s": 0.0011}}, "outputs": {"plots": ["attitude.pointing_error_deg", "orbit.theta_rad"]}},
        recommended=False,
    ),

    _template(
        "whole_spacecraft_unified_native", "整星统一Basilisk运行图", "统一原生运行图", "whole_spacecraft",
        "whole_spacecraft.unified_native.v1", "官方Basilisk模块与项目自研SysModel在同一Process/Task中完成轨道、ADCS、电源、数据和热控消息耦合。",
        duration_s=120.0,
        patch={"simulation": {"sample_s": 2.0, "step_s": 0.2, "backend": "basilisk"}, "assurance": {"allow_proxy": False}, "parameters": {"values": {"initial_pointing_error_deg": 8.0, "initial_soc": 0.62, "payload_max_pointing_error_deg": 20.0}}, "outputs": {"plots": ["adcs.pointing_error_deg", "eps.battery_soc", "data.storage_bits", "thermal.payload_temp_k"]}},
        recommended=True,
    ),

    # Six subsystem templates
    _template(
        "bsksim_whole_spacecraft_strong_coupling", "BSKSim整星强耦合场景", "BSKSim内核", "whole_spacecraft",
        "whole_spacecraft.bsksim_coupled.v1", "轨道/姿态—EPS—热控—通信/载荷—推进在同一 BSKSim-style 场景计划内形成工程强耦合。",
        duration_s=300.0,
        patch={"simulation": {"sample_s": 10.0, "step_s": 1.0, "backend": "python"}, "assurance": {"allow_proxy": True}, "parameters": {"values": {"initial_soc": 0.62, "eclipse_fraction": 0.35, "initial_pointing_error_deg": 8.0, "solar_power_max_w": 95.0, "payload_data_rate_bps": 2500000.0, "downlink_rate_max_bps": 1500000.0}}, "outputs": {"plots": ["eps.battery_soc", "eps.solar_array_power_w", "thermal.bus_temp_c", "data.storage_bits", "adcs.pointing_error_deg", "comm.downlink_rate_bps"]}},
        recommended=False,
    ),

    _template(
        "adcs_unified_native", "ADCS统一Basilisk闭环", "统一原生运行图", "subsystem",
        "subsystem.adcs_unified_native.v1", "官方星敏、IMU、磁强计、制导控制和反作用轮与项目传感器融合SysModel在同一Task中闭环运行。",
        duration_s=60.0,
        patch={"simulation": {"sample_s": 1.0, "step_s": 0.2, "backend": "basilisk"}, "assurance": {"allow_proxy": False}, "parameters": {"values": {"initial_pointing_error_deg": 8.0}}, "outputs": {"plots": ["adcs.pointing_error_deg", "adcs.rw.speed_rad_s_0", "adcs.body_rate_rad_s_x"]}},
        recommended=True,
    ),

    _template(
        "adcs_unified_native_event_chain", "ADCS统一运行图故障退化", "统一原生运行图", "subsystem",
        "subsystem.adcs_unified_native.v1", "在统一Basilisk闭环内注入反作用轮卡滞、陀螺偏置和噪声退化，输出运行时物理证据。",
        duration_s=40.0,
        patch={
            "simulation": {"sample_s": 1.0, "step_s": 0.2, "backend": "basilisk"},
            "assurance": {"allow_proxy": False},
            "events": {
                "faults": [
                    {"id": "rw0_jam", "effect": "adcs_rw_jamming", "target": "adcs", "start_s": 10.0, "end_s": 20.0, "implementation": "auto", "delivery": "modifier", "parameters": {"wheel_index": 0, "brake_torque_nm": 0.2}},
                    {"id": "gyro_bias", "effect": "gyro_bias_step", "target": "adcs", "start_s": 22.0, "end_s": 32.0, "implementation": "auto", "delivery": "modifier", "parameters": {"bias_step_deg_s": [0.2, 0.0, 0.0]}},
                ],
                "degradations": [
                    {"id": "gyro_noise", "effect": "gyro_noise_increase", "target": "adcs", "start_s": 22.0, "end_s": 32.0, "implementation": "auto", "delivery": "modifier", "parameters": {"noise_scale": 50.0}}
                ],
            },
            "outputs": {"plots": ["adcs.pointing_error_deg", "adcs.rw.speed_rad_s_0", "adcs.sensor.gyro_measured_rad_s_x"]},
            "model": {"target": {"mode": "mixed"}},
        },
    ),

    _template(
        "whole_spacecraft_unified_native_fault_chain", "整星统一运行图故障链", "统一原生运行图", "whole_spacecraft",
        "whole_spacecraft.unified_native.v1", "在统一运行图内验证载荷关机、下行中断和电池容量损失的跨分系统传播。",
        duration_s=40.0,
        patch={
            "simulation": {"sample_s": 1.0, "step_s": 0.2, "backend": "basilisk"},
            "assurance": {"allow_proxy": False},
            "events": {"faults": [
                {"id": "payload_off", "effect": "payload_instrument_off", "target": "whole_spacecraft", "start_s": 8.0, "end_s": 14.0, "implementation": "auto", "delivery": "modifier", "parameters": {"fmea": {"severity": 7, "occurrence": 3, "detectability": 3}}},
                {"id": "link_loss", "effect": "comm_data_downlink_link_loss", "target": "whole_spacecraft", "start_s": 16.0, "end_s": 22.0, "implementation": "auto", "delivery": "modifier", "parameters": {"fmea": {"severity": 8, "occurrence": 4, "detectability": 4}}},
                {"id": "battery_loss", "effect": "eps_battery_capacity_loss", "target": "whole_spacecraft", "start_s": 24.0, "end_s": 34.0, "implementation": "auto", "delivery": "modifier", "parameters": {"remaining_capacity_ratio": 0.6, "fmea": {"severity": 9, "occurrence": 3, "detectability": 4}}},
            ]},
            "outputs": {
                "plots": ["eps.battery_soc", "payload.generated_bps", "comm.downlink_bps", "data.storage_bits"],
                "telemetry_streams": [
                    {"stream_id": "fault_fast", "sample_s": 1.0, "fields": ["payload.active", "payload.generated_bps", "comm.active", "comm.downlink_bps", "eps.battery_soc", "eps.battery_capacity_j", "label.fault_active", "event.active_effects"], "format": "jsonl"},
                    {"stream_id": "fault_housekeeping", "sample_s": 5.0, "fields": ["payload.active", "comm.active", "eps.battery_soc", "eps.battery_capacity_j"], "format": "csv"},
                ],
                "fmea": {"enabled": True, "formats": ["csv", "json"]},
            },
            "model": {"target": {"mode": "fault"}},
        },
    ),

    _template(
        "whole_spacecraft_unified_native_degradation_chain", "整星统一运行图退化链", "统一原生运行图", "whole_spacecraft",
        "whole_spacecraft.unified_native.v1", "在统一运行图内验证太阳阵列效率、热辐射能力退化和低电量安全阈值。",
        duration_s=40.0,
        patch={
            "simulation": {"sample_s": 1.0, "step_s": 0.2, "backend": "basilisk"},
            "assurance": {"allow_proxy": False},
            "parameters": {"values": {"payload_power_w": 120.0, "initial_soc": 0.62}},
            "events": {
                "degradations": [
                    {"id": "solar_loss", "effect": "solar_panel_efficiency_loss", "target": "whole_spacecraft", "start_s": 8.0, "end_s": 24.0, "parameters": {"remaining_efficiency_ratio": 0.35}},
                    {"id": "radiator_loss", "effect": "thermal_radiator_rejection_loss", "target": "whole_spacecraft", "start_s": 8.0, "end_s": 24.0, "parameters": {"remaining_rejection_ratio": 0.25}},
                ],
                "constraints": [
                    {"id": "safe_mode", "effect": "power_safe_mode_threshold", "target": "whole_spacecraft", "start_s": 26.0, "end_s": 34.0, "parameters": {"soc_threshold": 0.9}}
                ],
            },
            "outputs": {"plots": ["eps.solar_array_power_w", "eps.battery_soc", "thermal.payload_temp_k", "payload.active"]},
            "model": {"target": {"mode": "degradation"}},
        },
    ),

    _template(
        "whole_spacecraft_unified_native_constraint_chain", "整星统一运行图约束链", "统一原生运行图", "whole_spacecraft",
        "whole_spacecraft.unified_native.v1", "在统一运行图内单独验证低电量安全阈值约束及载荷安全模式响应。",
        duration_s=36.0,
        patch={
            "simulation": {"sample_s": 1.0, "step_s": 0.2, "backend": "basilisk"},
            "assurance": {"allow_proxy": False},
            "parameters": {"values": {"initial_soc": 0.62, "payload_power_w": 100.0}},
            "events": {"constraints": [
                {"id": "safe_mode_only", "effect": "power_safe_mode_threshold", "target": "whole_spacecraft", "start_s": 8.0, "end_s": 28.0, "parameters": {"soc_threshold": 0.9}}
            ]},
            "outputs": {"plots": ["eps.battery_soc", "payload.active", "eps.load_shed_active"]},
            "model": {"target": {"mode": "nominal"}},
        },
    ),

    _template(
        "adcs_pointing", "ADCS 姿态收敛", "分系统", "subsystem",
        "subsystem.adcs_fidelity.v1", "姿态指向收敛、陀螺测量与反作用轮状态。",
        patch={"simulation": {"sample_s": 2.0, "step_s": 0.25}},
        recommended=True,
    ),

    _template(
        "adcs_bsksim_migration", "ADCS BSKSim-style迁移桥接", "BSKSim内核", "subsystem",
        "subsystem.adcs_bsksim.v1", "将ADCS纳入项目自有BSKSim-style场景—动力学—飞控—事件—记录器合同，保留已验证ADCS传播作为物理桥接。",
        duration_s=60.0,
        patch={"simulation": {"sample_s": 2.0, "step_s": 0.25, "backend": "python"}, "assurance": {"allow_proxy": True}, "parameters": {"values": {"target_mode": "nadir", "wheel_configuration": "pyramid_4"}}, "outputs": {"plots": ["adcs.attitude.pointing_error_deg", "adcs.rw.speed_rad_s_*", "adcs.control.applied_torque_nm_*"]}},
        recommended=False,
    ),

    _template(
        "eps_eclipse", "EPS 食段供电", "分系统", "subsystem",
        "subsystem.eps.basic.v1", "太阳阵列在食段失去输入时，电池承担平台负载并产生 SOC 变化。",
        duration_s=120.0,
        patch={"simulation": {"sample_s": 5.0, "step_s": 5.0}, "parameters": {"values": {"eclipse_period_s": 120.0, "eclipse_duration_s": 50.0, "eclipse_start_s": 20.0}}},
    ),
    _template(
        "eps_unified_native", "EPS统一Basilisk运行图", "统一原生运行图", "subsystem",
        "subsystem.eps.unified_native.v1", "官方 Basilisk 电源节点与项目 EPS SysModel 在同一任务中运行，并输出原生多速率遥测。",
        duration_s=60.0,
        patch={
            "simulation": {"sample_s": 1.0, "step_s": 1.0, "backend": "basilisk"},
            "assurance": {"allow_proxy": False},
            "parameters": {"values": {"initial_soc": 0.62, "battery_capacity_wh": 160.0, "solar_power_w": 95.0}},
            "outputs": {
                "plots": ["eps.battery_soc", "eps.solar_power_w", "eps.net_power_w"],
                "telemetry_streams": [
                    {"stream_id": "eps_fast", "sample_s": 1.0, "fields": ["eps.battery_soc", "eps.net_power_w"], "format": "csv"},
                    {"stream_id": "eps_housekeeping", "sample_s": 10.0, "fields": ["eps.battery_soc", "eps.solar_power_w", "eps.load_shed_active"], "format": "jsonl"},
                ],
                "fmea": {"enabled": True, "formats": ["csv", "json"]},
            },
        },
        recommended=True,
    ),
    _template(
        "eps_source_native", "EPS工程源模型", "分系统", "subsystem",
        "subsystem.eps.source_native.v1", "使用项目现有 EPS builder/model API 运行工程级电源分系统模型。",
        duration_s=60.0,
        patch={"simulation": {"sample_s": 2.0, "step_s": 2.0, "backend": "python"}, "outputs": {"plots": ["eps.source_native.battery_soc", "eps.source_native.net_power_w"]}},
    ),
    _template(
        "comm_data_unified_native", "通信数据统一Basilisk运行图", "统一原生运行图", "subsystem",
        "subsystem.comm_data.unified_native.v1", "官方 Basilisk 数据节点与项目通信数据 SysModel 在同一任务中运行，并输出原生多速率遥测。",
        duration_s=60.0,
        patch={
            "simulation": {"sample_s": 1.0, "step_s": 1.0, "backend": "basilisk"},
            "assurance": {"allow_proxy": False},
            "parameters": {"values": {"instrument_baud_bps": 2500000.0, "transmitter_baud_bps": 1500000.0, "storage_capacity_bits": 6000000000.0, "native_storage_drain_enabled": True}},
            "outputs": {
                "plots": ["comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_baud_bps"],
                "telemetry_streams": [
                    {"stream_id": "data_fast", "sample_s": 1.0, "fields": ["comm_data.storage_level_bits", "comm_data.transmitter_storage_node_baud_bps"], "format": "csv"},
                    {"stream_id": "data_summary", "sample_s": 10.0, "fields": ["comm_data.storage_level_bits", "comm_data.instrument_baud_bps", "comm_data.transmitter_baud_bps"], "format": "jsonl"},
                ],
                "fmea": {"enabled": True, "formats": ["csv", "json"]},
            },
        },
        recommended=True,
    ),
    _template(
        "comm_data_source_native", "通信数据工程源模型", "分系统", "subsystem",
        "subsystem.comm_data.source_native.v1", "使用项目现有通信数据 builder/model API 运行工程级队列和下行模型。",
        duration_s=60.0,
        patch={"simulation": {"sample_s": 2.0, "step_s": 2.0, "backend": "python"}, "outputs": {"plots": ["comm_data.source_native.queue_bits", "comm_data.source_native.downlink_bps"]}},
    ),

    _template(
        "thermal_control", "热控加热器控制", "分系统", "subsystem",
        "subsystem.thermal.basic_lumped.v1", "低温初始条件下，加热器阈值控制与平台/电池温度响应。",
        duration_s=120.0,
        patch={"simulation": {"sample_s": 5.0, "step_s": 5.0}, "parameters": {"values": {"initial_bus_temp_c": 2.0, "initial_battery_temp_c": 1.0, "heater_setpoint_c": 5.0}}},
    ),
    _template(
        "thermal_source_native", "热控工程源模型", "分系统", "subsystem",
        "subsystem.thermal.source_native.v1", "使用项目现有热控 builder/model API 运行平台与载荷集总热模型。",
        duration_s=120.0,
        patch={"simulation": {"sample_s": 5.0, "step_s": 5.0, "backend": "python"}, "outputs": {"plots": ["thermal.source_native.electronics_temp_k", "thermal.source_native.payload_temp_k", "thermal.source_native.heater_power_w"]}},
    ),

    _template(
        "comm_downlink", "通信数据积压与下行", "分系统", "subsystem",
        "subsystem.comm.basic_ground_pass.v1", "地面站可见窗口、数据积压和下行链路能力联合仿真。",
        duration_s=120.0,
        patch={"simulation": {"sample_s": 5.0, "step_s": 5.0}, "parameters": {"values": {"initial_backlog_bits": 1000000.0}}},
    ),
    _template(
        "propulsion_burn", "推进分系统点火", "分系统", "subsystem",
        "subsystem.propulsion.unified_native.v1", "使用官方 Basilisk 推力器与燃料贮箱模块执行一次聚焦点火。",
        patch={"simulation": {"duration_s": 3.0, "step_s": 0.1, "sample_s": 0.1, "backend": "basilisk"}, "assurance": {"allow_proxy": False}, "parameters": {"values": {"initial_propellant_kg": 1.0, "tank_capacity_kg": 2.0, "burn_start_s": 0.5, "burn_on_time_s": 0.5}}},
        recommended=True,
    ),
    _template(
        "payload_observation", "载荷观测任务", "分系统", "subsystem",
        "subsystem.payload.source_native.v1", "在姿态、热控和电源许可条件下执行载荷观测并生成数据。",
        patch={"parameters": {"values": {"eps_allows_payload": True, "thermal_allows_payload": True, "pointing_error_deg": 0.05}}},
    ),

    # Coupled and whole-spacecraft templates
    _template(
        "orbit_adcs_coupled", "轨道—姿态联合仿真", "整星联合", "whole_spacecraft",
        "whole_spacecraft.orbit_adcs_fidelity.v1", "轨道传播、环境扰动和姿态控制的联合运行。",
        duration_s=120.0,
    ),
    _template(
        "maneuver_orbit_attitude", "推进—轨道—姿态机动联合仿真", "整星联合", "whole_spacecraft",
        "whole_spacecraft.maneuver_orbit_attitude.v1", "有限时长推进点火、推进剂消耗、轨道速度变化代理和姿态扰动控制的工程联合场景。",
        duration_s=180.0,
        patch={
            "simulation": {"sample_s": 10.0, "step_s": 10.0, "backend": "python"},
            "parameters": {"values": {"burn_start_s": 40.0, "burn_duration_s": 40.0, "thrust_n": 0.45, "initial_propellant_kg": 8.0}},
            "outputs": {"plots": ["propulsion.propellant.remaining_kg", "propulsion.delta_v.cumulative_m_s", "orbit.speed_delta_proxy_m_s", "adcs.pointing_error_deg"]},
        },
    ),

    _template(
        "power_thermal_orbit_coupled", "功率—热—轨道联合仿真", "整星联合", "whole_spacecraft",
        "whole_spacecraft.power_thermal_orbit_coupled.v1", "轨道光照驱动下的电源与热控耦合运行。",
        duration_s=120.0,
    ),
    _template(
        "comm_payload_mission", "通信—载荷任务链", "整星联合", "whole_spacecraft",
        "whole_spacecraft.comm_payload_mission_coupled.v1", "载荷数据生成、星上存储和地面下行的任务链闭环。",
        duration_s=120.0,
    ),
    _template(
        "whole_nominal", "完整整星正常运行", "整星", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "六分系统联合正常运行与守恒检查。",
        assertions=[
            {"metric": "status", "operator": "==", "value": "PASS"},
            {"metric": "energy_conservation_status", "operator": "==", "value": "PASS"},
            {"metric": "data_conservation_status", "operator": "==", "value": "PASS"},
        ],
        recommended=True,
    ),
    _template(
        "whole_battery_loss", "电池容量突降", "整星故障", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "20 秒注入电池容量损失。",
        patch={"events": {"faults": [{"id": "battery_loss", "event_type": "fault", "target": "whole_spacecraft", "effect": "eps_battery_capacity_loss", "start_s": 20.0, "magnitude": 0.3, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "fault"}}},
    ),
    _template(
        "whole_rw_jam", "反作用轮卡滞", "整星故障", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "20 秒注入反作用轮卡滞。",
        patch={"events": {"faults": [{"id": "rw_jam", "event_type": "fault", "target": "whole_spacecraft", "effect": "adcs_rw_jamming", "start_s": 20.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "fault"}}},
    ),
    _template(
        "whole_payload_off", "载荷关机", "整星故障", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "20 秒关闭载荷并检查数据生成中断。",
        patch={"events": {"faults": [{"id": "payload_off", "event_type": "fault", "target": "whole_spacecraft", "effect": "payload_instrument_off", "start_s": 20.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "fault"}}},
    ),
    _template(
        "whole_comm_loss", "通信中断与恢复", "整星故障", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "20 至 40 秒下行中断。",
        patch={"events": {"faults": [{"id": "comm_loss", "event_type": "fault", "target": "whole_spacecraft", "effect": "comm_data_downlink_link_loss", "start_s": 20.0, "end_s": 40.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "fault"}}},
    ),
    _template(
        "whole_thermal_fault", "热控异常", "整星故障", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "20 至 40 秒加热器常开。",
        patch={"events": {"faults": [{"id": "heater_on", "event_type": "fault", "target": "whole_spacecraft", "effect": "thermal_heater_stuck_on", "start_s": 20.0, "end_s": 40.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "fault"}}},
    ),
    _template(
        "whole_eol", "多分系统寿命末期退化", "整星退化", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "从仿真开始施加多分系统寿命末期退化。",
        patch={"events": {"degradations": [{"id": "eol", "event_type": "degradation", "target": "whole_spacecraft", "effect": "multi_subsystem_end_of_life", "start_s": 0.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "degradation"}}},
    ),
    _template(
        "whole_mixed", "故障与退化组合", "整星组合", "whole_spacecraft",
        "whole_spacecraft.composite_digital_twin.v1", "太阳阵列退化并在 30 秒关闭载荷。",
        patch={"events": {"degradations": [{"id": "solar_loss", "event_type": "degradation", "target": "whole_spacecraft", "effect": "solar_panel_efficiency_loss_20pct", "start_s": 0.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}], "faults": [{"id": "payload_off", "event_type": "fault", "target": "whole_spacecraft", "effect": "payload_instrument_off", "start_s": 30.0, "implementation": "auto", "delivery": "modifier", "parameters": {}}]}, "model": {"target": {"mode": "mixed"}}},
    ),
    # Additional direct component templates: every independently executable component has at least one template.
    _template(
        "component_antenna_nominal", "天线增益与指向损失", "部件", "component",
        "component.antenna.v1", "天线在给定增益、指向误差和损耗条件下的有效链路增益。",
    ),
    _template(
        "component_ground_station_access", "地面站可见性", "部件", "component",
        "component.ground_station.v1", "地面站位置、最低仰角和最大距离约束下的可见性判定。",
    ),
    _template(
        "component_heater_switch", "加热器开关与功耗", "部件", "component",
        "component.heater.v1", "加热器在温度阈值控制下的开关状态和功耗响应。",
    ),
    _template(
        "component_link_budget_nominal", "链路预算计算", "部件", "component",
        "component.link_budget.v1", "根据发射功率、天线增益、距离、频率和噪声参数计算链路余量。",
    ),
    _template(
        "component_onboard_storage_fill", "星上存储占用", "部件", "component",
        "component.onboard_storage.v1", "数据写入与读取条件下的存储占用、溢出和剩余容量。",
    ),
    _template(
        "component_payload_instrument", "载荷仪器工作模式", "部件", "component",
        "component.payload.v1", "载荷仪器在待机与观测模式下的功耗和数据生成。",
    ),
    _template(
        "component_power_sink_profile", "功率负载时序", "部件", "component",
        "component.power_sink.v1", "单个功率负载在工作模式切换下的功耗时序。",
    ),
    _template(
        "component_radiator_rejection", "散热器热排散", "部件", "component",
        "component.radiator.v1", "散热器面积、发射率和温差共同决定的热排散能力。",
    ),
    _template(
        "component_transmitter_downlink", "发射机工作与功耗", "部件", "component",
        "component.transmitter.v1", "发射机在待机和发射状态下的有效码率与功耗。",
    ),

    # Independent component templates backed by source-native public APIs.
    _template(
        "component_cmg_nominal", "控制力矩陀螺力矩响应", "部件", "component",
        "component.cmg.v1", "独立 CMG 部件模型，输出轮速、框架角和角动量。",
        object_id="component.cmg", execution_scope="direct",
    ),
    _template(
        "component_imu_nominal", "惯性测量单元正常测量", "部件", "component",
        "component.imu.v1", "独立 IMU 部件模型，输出角速度和加速度测量。",
        object_id="component.imu", execution_scope="direct",
    ),
    _template(
        "component_magnetometer_nominal", "磁强计正常测量", "部件", "component",
        "component.magnetometer.v1", "独立磁强计部件模型，输出三轴磁场和模长。",
        object_id="component.magnetometer", execution_scope="direct",
    ),
    _template(
        "component_mtb_nominal", "磁力矩器磁矩与力矩响应", "部件", "component",
        "component.mtb.v1", "独立磁力矩器部件模型：设置磁偶极矩和地磁场，输出实际磁矩、m×B 力矩及饱和状态。",
        patch={"parameters": {"values": {
            "command_mode": "direct_dipole",
            "command_dipole_am2": [0.1, 0.0, 0.0],
            "magnetic_field_t": [2.0e-5, -1.0e-5, 3.0e-5],
        }}},
        object_id="component.mtb", execution_scope="direct",
    ),
    _template(
        "component_pdu_nominal", "配电单元负载卸载", "部件", "component",
        "component.pdu.v1", "独立 PDU 部件模型，按母线功率上限和顺序执行负载卸载。",
        object_id="component.pdu", execution_scope="direct",
    ),
    _template(
        "component_star_tracker_nominal", "星敏感器正常测量", "部件", "component",
        "component.star_tracker.v1", "独立星敏感器部件模型，输出姿态 MRP、有效状态和漂移。",
        object_id="component.star_tracker", execution_scope="direct",
    ),
    _template(
        "component_sun_sensor_nominal", "太阳敏感器正常测量", "部件", "component",
        "component.sun_sensor.v1", "独立太阳敏感器部件模型，输出太阳矢量、强度和有效状态。",
        object_id="component.sun_sensor", execution_scope="direct",
    ),
    _template(
        "component_fuel_tank_nominal", "燃料贮箱正常供给", "部件", "component",
        "component.fuel_tank.v1", "独立燃料贮箱模型，输出推进剂质量、工程压力和消耗量。",
        object_id="component.fuel_tank", execution_scope="direct",
    ),
    _template(
        "component_thruster_nominal", "推力器正常点火", "部件", "component",
        "component.thruster.v1", "独立推力器脉冲模型，输出累计冲量和推进剂消耗。",
        object_id="component.thruster", execution_scope="direct",
    ),

]


def _product_visible_template(template: Mapping[str, Any], *, include_compatibility: bool, include_explicit: bool) -> bool:
    try:
        contract = get_capability(str(template.get("capability_id") or ""))
    except Exception:
        return False
    lifecycle = contract.lifecycle_status
    tier = contract.product_tier
    if lifecycle == "blocked" or tier == "blocked":
        return False
    if lifecycle in {"deprecated", "internal", "archived"} or tier == "compatibility":
        return include_compatibility
    if tier == "explicit":
        return include_explicit
    return True


def list_scenario_templates(
    *,
    level: str | None = None,
    capability_id: str | None = None,
    object_id: str | None = None,
    product_visible_only: bool = False,
    include_compatibility: bool = False,
    include_explicit: bool = False,
) -> dict[str, Any]:
    templates = copy.deepcopy(_TEMPLATES)
    if product_visible_only:
        templates = [
            item for item in templates
            if _product_visible_template(
                item,
                include_compatibility=include_compatibility,
                include_explicit=include_explicit,
            )
        ]
    if level:
        templates = [item for item in templates if item["level"] == level]
    if capability_id:
        templates = [item for item in templates if item["capability_id"] == capability_id]
    if object_id:
        templates = [item for item in templates if item.get("object_id") == object_id]
    return {"schema_version": SCENARIO_TEMPLATE_SCHEMA_VERSION, "count": len(templates), "templates": templates}


def get_scenario_template(template_id: str) -> dict[str, Any] | None:
    return next((copy.deepcopy(item) for item in _TEMPLATES if item["template_id"] == template_id), None)


def _deep_merge(base: dict[str, Any], override: Mapping[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, Mapping) and isinstance(base.get(key), dict):
            _deep_merge(base[key], value)
        else:
            base[key] = copy.deepcopy(value)
    return base


def instantiate_scenario_template(template_id: str, *, task_id: str | None = None, name: str | None = None) -> dict[str, Any]:
    template = get_scenario_template(template_id)
    if template is None:
        raise KeyError(template_id)
    form = copy.deepcopy(capability_form_schema(template["capability_id"])["default_form"])
    resolved_task_id = task_id or f"template_{template_id}"
    form["task"]["id"] = resolved_task_id
    form["task"]["name"] = name or template["name"]
    form["task"]["description"] = template["summary"]
    form["simulation"]["duration_s"] = template["duration_s"]
    form["outputs"]["output_root"] = f"runs/{resolved_task_id}"
    _deep_merge(form, template["patch"])
    if template["assertions"]:
        form.setdefault("model", {}).setdefault("validation", {})["acceptance_assertions"] = copy.deepcopy(template["assertions"])
    spec = normalize_form_task_spec(
        form,
        task_id=resolved_task_id,
        output_root=form["outputs"]["output_root"],
    )
    validation = validate_task_spec(spec)
    guards = evaluate_agent_guards(spec)
    planning = plan_task_spec(spec) if validation.ok and guards.ok else None
    if not validation.ok or not guards.ok or planning is None or not planning.ok:
        reasons = [issue.code for issue in validation.issues]
        reasons.extend(issue.code for issue in guards.issues)
        if planning is not None:
            reasons.extend(issue.code for issue in planning.validation.issues)
        raise ValueError(f"template normalization failed: {template_id}; reasons={list(dict.fromkeys(reasons))}")
    spec = canonicalize_task_spec(spec)
    spec.setdefault("metadata", {})["scenario_template"] = {
        "schema_version": SCENARIO_TEMPLATE_SCHEMA_VERSION,
        "template_id": template_id,
        "name": template["name"],
        "level": template["level"],
        "object_id": template.get("object_id"),
        "execution_scope": template.get("execution_scope", "direct"),
        "runtime_test_required": bool(template.get("runtime_test_required", True)),
        "expected_validation": str(template.get("expected_validation") or "PASS"),
        "expected_mission_status": template.get("expected_mission_status"),
        "expected_validation_reason_code": template.get("expected_validation_reason_code"),
    }
    return canonicalize_task_spec(spec)


__all__ = ["SCENARIO_TEMPLATE_SCHEMA_VERSION", "list_scenario_templates", "get_scenario_template", "instantiate_scenario_template"]
