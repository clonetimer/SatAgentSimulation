"""BSK-RL-style event catalog for sat-sim fault episodes."""
from __future__ import annotations

from .fault_effect_contract import FaultEffectContract


def _contract(
    effect: str,
    category: str,
    display_name_zh: str,
    target_kind: str,
    trigger_semantics: str,
    expected_observables: tuple[str, ...],
    parameter_hints: dict | None = None,
    evidence_policy: str = "state_and_observable_effect",
) -> FaultEffectContract:
    return FaultEffectContract(
        effect=effect,
        category=category,
        display_name_zh=display_name_zh,
        target_kind=target_kind,
        trigger_semantics=trigger_semantics,
        expected_observables=expected_observables,
        evidence_policy=evidence_policy,
        parameter_hints=dict(parameter_hints or {}),
    )


_CATALOG: dict[str, FaultEffectContract] = {
    "rw_jamming": _contract(
        "rw_jamming",
        "fault",
        "反作用轮卡滞",
        "reaction_wheel",
        "二值故障；事件时间窗内目标轮进入卡滞状态。",
        ("label.fault_active", "adcs.event.active_effects", "adcs.rw.speed_rad_s_*", "adcs.rw.command_torque_nm_*"),
        {"wheel_index": "目标轮号；从0开始", "magnitude": "兼容旧任务；新任务忽略"},
    ),
    "adcs_rw_jamming": _contract(
        "adcs_rw_jamming",
        "fault",
        "ADCS反作用轮卡滞",
        "reaction_wheel",
        "二值故障；闭环ADCS中目标反作用轮卡滞。",
        ("label.fault_active", "adcs.event.active_effects", "event.active_effects", "adcs.rw.speed_rad_s_*", "adcs.control.applied_torque_nm_*", "adcs.pointing_error_deg", "adcs.pointing_quality", "adcs.power_w", "comm.downlink_rate_bps"),
        {"wheel_index": "目标轮号；从0开始"},
    ),
    "rw_motor_failure": _contract(
        "rw_motor_failure",
        "fault",
        "反作用轮电机失效",
        "reaction_wheel",
        "二值故障；事件时间窗内目标轮可用电机力矩降低或清零。",
        ("label.fault_active", "adcs.rw.effective_max_torque_nm_*", "adcs.rw.command_torque_nm_*"),
        {"wheel_index": "目标轮号；从0开始", "torque_scale": "可选；0表示完全失效"},
    ),
    "adcs_rw_motor_failure": _contract(
        "adcs_rw_motor_failure",
        "fault",
        "ADCS反作用轮电机失效",
        "reaction_wheel",
        "二值故障；闭环ADCS中目标轮可用力矩降低。",
        ("label.fault_active", "adcs.rw.effective_max_torque_nm_*", "adcs.event.active_effects"),
        {"wheel_index": "目标轮号；从0开始", "torque_scale": "可选；0表示完全失效"},
    ),
    "adcs_rw_torque_authority_loss": _contract(
        "adcs_rw_torque_authority_loss",
        "degradation",
        "ADCS反作用轮力矩权限下降",
        "reaction_wheel",
        "性能退化；事件时间窗内目标轮仅保留指定比例的标称可用力矩。",
        ("label.degradation_active", "adcs.rw.effective_max_torque_nm_*", "adcs.control.applied_torque_nm_*", "adcs.event.active_effects"),
        {"wheel_index": "目标轮号；从0开始", "torque_scale": "剩余可用力矩比例；范围0到1"},
    ),
    "gyro_bias_step": _contract(
        "gyro_bias_step",
        "fault",
        "陀螺偏置突变",
        "gyro",
        "参数突变故障；事件开始后陀螺测量偏置发生阶跃。",
        ("label.fault_active", "adcs.sensor.gyro_bias_rad_s_*", "adcs.sensor.gyro_measured_rad_s_*"),
        {"bias_step_deg_s": "偏置增量，deg/s；可为单值或三轴数组"},
    ),
    "gyro_noise_increase": _contract(
        "gyro_noise_increase",
        "degradation",
        "陀螺噪声增大",
        "gyro",
        "性能退化；事件时间窗内陀螺随机噪声标准差放大。",
        ("label.degradation_active", "adcs.sensor.effective_gyro_noise_scale", "adcs.sensor.gyro_noise_rad_s_*"),
        {"noise_scale": "噪声放大倍数；必须大于等于1"},
    ),
    "rw_friction_degradation": _contract(
        "rw_friction_degradation",
        "degradation",
        "反作用轮摩擦退化",
        "reaction_wheel",
        "性能退化；目标轮附加等效阻力矩。",
        ("label.degradation_active", "adcs.rw.effective_drag_nms_*", "adcs.rw.speed_rad_s_*"),
        {"wheel_index": "目标轮号；从0开始", "drag_nms": "附加等效阻尼，N·m·s"},
    ),
    "reaction_wheel_speed_limit": _contract(
        "reaction_wheel_speed_limit",
        "constraint",
        "反作用轮速度限制",
        "reaction_wheel",
        "运行约束；目标轮有效最高转速降低，不作为故障。",
        ("label.constraint_active", "adcs.rw.effective_max_speed_rad_s_*", "adcs.rw.speed_rad_s_*"),
        {"wheel_index": "目标轮号；从0开始", "max_speed_rad_s": "约束后最高转速，rad/s"},
    ),
    "adcs_reaction_wheel_speed_limit": _contract(
        "adcs_reaction_wheel_speed_limit",
        "constraint",
        "ADCS反作用轮速度限制",
        "reaction_wheel",
        "运行约束；闭环ADCS中目标轮有效最高转速降低，不作为故障。",
        ("label.constraint_active", "adcs.rw.effective_max_speed_rad_s_*", "adcs.event.active_effects"),
        {"wheel_index": "目标轮号；从0开始", "max_speed_rad_s": "约束后最高转速，rad/s"},
    ),
    "payload_instrument_off": _contract(
        "payload_instrument_off", "fault", "载荷关机", "payload",
        "二值故障；事件时间窗内载荷功耗和数据生成在状态积分前关闭。",
        ("label.fault_active", "event.active_effects", "payload.active", "payload.generated_data_bits", "payload.generated_bps", "data.storage_bits"),
    ),
    "comm_data_downlink_link_loss": _contract(
        "comm_data_downlink_link_loss", "fault", "下行链路中断", "communication",
        "二值故障；事件时间窗内下行速率和下行数据量在存储积分前清零。",
        ("label.fault_active", "event.active_effects", "comm.downlink_rate_bps", "comm.downlink_bps", "comm.downlinked_bits", "data.storage_bits"),
    ),
    "eps_battery_capacity_loss": _contract(
        "eps_battery_capacity_loss", "fault", "电池容量损失", "battery",
        "容量状态突变；有效容量变化进入电池能量和SOC积分。",
        ("label.fault_active", "event.active_effects", "eps.effective_battery_capacity_wh", "eps.battery_capacity_j", "eps.battery_energy_wh", "eps.battery_energy_j", "eps.battery_soc"),
    ),
    "thermal_radiator_rejection_loss": _contract(
        "thermal_radiator_rejection_loss", "degradation", "散热能力退化", "radiator",
        "散热系数退化；有效散热系数变化进入热状态积分。",
        ("label.degradation_active", "event.active_effects", "thermal.effective_radiator_coeff_w_per_k", "thermal.bus_temp_c", "thermal.payload_temp_k", "thermal.payload_temp_c"),
    ),
    "solar_panel_efficiency_loss": _contract(
        "solar_panel_efficiency_loss", "degradation", "太阳阵列效率下降", "solar_panel",
        "发电效率退化；效率比例进入太阳阵列功率与电池积分。",
        ("label.degradation_active", "event.active_effects", "eps.solar_efficiency_scale", "eps.solar_array_power_w", "eps.battery_soc"),
    ),
    "power_safe_mode_threshold": _contract(
        "power_safe_mode_threshold", "constraint", "低电量安全阈值", "power_manager",
        "运行约束；SOC低于阈值时在负载和数据积分前关闭载荷与下行。",
        ("label.constraint_active", "label.power_safe_mode_engaged", "payload.active", "comm.downlink_rate_bps", "eps.battery_soc"),
    ),
    "propulsion_thruster_ignition_failure": _contract(
        "propulsion_thruster_ignition_failure", "fault", "推进器点火失败", "propulsion",
        "二值故障；计划燃烧时窗内的推进器点火指令在进入动力学模型前被抑制。",
        (
            "label.fault_active", "event.active_effects",
            "propulsion.burn_active", "propulsion.total_thrust_n",
            "propulsion.fuel_mass_kg", "orbit.radius_m",
        ),
    ),
    "propulsion_burn_impulse_loss": _contract(
        "propulsion_burn_impulse_loss", "degradation", "推进燃烧冲量退化", "propulsion",
        "性能退化；计划燃烧的有效脉宽按剩余冲量比例缩短，并进入推力、耗剂、轨道、供电和热耦合。",
        (
            "label.degradation_active", "event.active_effects",
            "propulsion.burn_active", "propulsion.total_thrust_n",
            "propulsion.fuel_mass_kg", "propulsion.electrical_power_w",
            "orbit.radius_m",
        ),
        {"remaining_impulse_ratio": "剩余可用燃烧冲量比例；范围0到1"},
    ),
    "bsksim_fault_marker": _contract(
        "bsksim_fault_marker",
        "fault",
        "BSKSim基础故障标记",
        "spacecraft",
        "基础场景中的故障标记事件，用于验证事件时间窗和记录器链路。",
        ("label.fault_active",),
        evidence_policy="timeline_only",
    ),
}


def get_fault_contract(effect: str, category: str | None = None) -> FaultEffectContract:
    key = str(effect)
    if key in _CATALOG:
        contract = _CATALOG[key]
        if category and contract.category != category:
            # Preserve the caller's category if an old alias was routed differently.
            return FaultEffectContract(
                effect=contract.effect,
                category=str(category),
                display_name_zh=contract.display_name_zh,
                target_kind=contract.target_kind,
                trigger_semantics=contract.trigger_semantics,
                expected_observables=contract.expected_observables,
                evidence_policy=contract.evidence_policy,
                parameter_hints=contract.parameter_hints,
            )
        return contract
    display = {"fault": "未登记故障", "degradation": "未登记退化", "constraint": "未登记运行约束"}.get(str(category), "未登记事件")
    return FaultEffectContract(
        effect=key,
        category=str(category or "event"),
        display_name_zh=f"{display}：{key}",
        target_kind="unknown",
        trigger_semantics="未在故障环境目录中登记；仅按事件时间窗记录。",
        expected_observables=(f"label.{category}_active",) if category else (),
        evidence_policy="timeline_only",
    )


def list_fault_contracts() -> list[dict]:
    return [c.to_dict() for c in _CATALOG.values()]


__all__ = ["get_fault_contract", "list_fault_contracts"]
