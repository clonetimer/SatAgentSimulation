"""Shared literal aliases; parameter names are not mission-time fields."""
EXPLICIT_PARAMETER_ALIASES: dict[str, tuple[str, ...]] = {
    "simulation.duration_s": ("仿真时间", "运行时间", "持续时间", "duration", "run time"),
    "parameters.values.battery_capacity_wh": ("电池容量", "battery capacity"),
    "parameters.values.initial_soc": ("初始soc", "初始荷电状态", "initial soc", "state of charge"),
    "parameters.values.solar_power_w": ("太阳翼功率", "太阳阵列功率", "solar power"),
    "parameters.values.payload_power_w": ("载荷功耗", "载荷功率", "payload power"),
    "parameters.values.storage_capacity_bits": ("存储容量", "storage capacity"),
    "parameters.values.transmitter_baud_bps": ("数据率", "码率", "baud", "data rate"),
    "parameters.values.thermal_step_s": ("热控步长", "热仿真步长", "thermal step"),
    "parameters.values.orb_env_step_s": ("轨道环境步长", "环境步长", "orbit environment step"),
    "parameters.values.adcs_dyn_step_s": ("动力学步长", "姿态动力学步长", "dynamics step"),
    "parameters.values.adcs_fsw_step_s": ("控制周期", "飞控周期", "fsw step", "control period"),
}
