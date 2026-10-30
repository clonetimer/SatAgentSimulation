# 原生资源损失事件的幅度语义

通用近地小卫星工程场景中的 `eps_battery_capacity_loss`、`solar_panel_efficiency_loss` 和 `thermal_radiator_rejection_loss` 支持标量损失幅度。规范事件的 `magnitude` 与兼容修饰器的 `severity` 使用同一无量纲区间 `[0,1]`：0 表示零损失，0.3 表示损失基准量的 30%，1 表示完全损失。原生事件解析将其转换为 `remaining_ratio = 1 - magnitude`，由既有物理参数事件控制器消费。

这是一条明确的工程配置语义，不是实际电池老化、太阳翼退化或散热器故障机理的飞行标定。它只规定基准容量、转换效率或散热能力系数如何变化；热、电与姿态响应仍由原生模型演化产生。其他类型事件使用各自的单位和参数，不能把这条资源损失规则推广成所有故障的通用线性机理。

已有显式参数 `remaining_capacity_ratio`、`remaining_efficiency_ratio`、`remaining_rejection_ratio`、`remaining_ratio`、`loss_fraction` 或 `loss_pct` 保留原有优先级，不被标量覆盖。没有给出标量和这些参数时，仍使用该效果既有默认值。非数值、布尔值、非有限数和区间外标量在资源损失解析阶段被拒绝。输入 TaskSpec 和模型原始回答不改写；派生比例记录在实际运行事件参数中。

2026-10-29 的受控原生运行发现：旧解析器丢弃顶层幅度，`magnitude=0.1` 和 `0.3` 都执行为默认 50% 容量损失。失败记录位于 `reports/local_requirements_completion/native-event-magnitude-physical-repro/report.json`。修复后的真实 SPICE 原生运行分别得到剩余容量 90% 和 70%，显式 `loss_fraction=0.3` 对照也为 70%；记录位于 `native-event-magnitude-physical-fixed-file-entry/report.json`。运行前后物理源码均为 `84fa8bbd713f5b7aa80bd5095f658e70dedc042613feaa0448dcd79b63f10d82`。

37 项解析合同测试覆盖两种事件表示、三类资源损失、区间端点、非法幅度及显式参数优先级；额外原生测试仍须按效果和工况分别扩充。上述三次容量实测不构成所有损失机理的硬件验证。新源码的完整回归、软实时性能、热工况和同一实例真实 24 小时耐久分别验收，既有版本的通过记录不自动继承。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_validation; phase=resource_loss_mapping_requalification; evidence=actual_native_capacity_mapping_verified; completion=unproven -->
