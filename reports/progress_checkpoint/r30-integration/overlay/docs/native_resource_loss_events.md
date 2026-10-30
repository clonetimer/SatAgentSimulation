# 原生资源损失事件的幅度语义

通用近地小卫星工程场景中的 `eps_battery_capacity_loss`、`solar_panel_efficiency_loss` 和 `thermal_radiator_rejection_loss` 支持标量损失幅度。规范事件的 `magnitude` 与兼容修饰器的 `severity` 使用同一无量纲区间 `[0,1]`：0 表示零损失，0.3 表示损失基准量的 30%，1 表示完全损失。原生事件解析将其转换为 `remaining_ratio = 1 - magnitude`，由既有物理参数事件控制器消费。

这是一条明确的工程配置语义，不是实际电池老化、太阳翼退化或散热器故障机理的飞行标定。它只规定基准容量、转换效率或散热能力系数如何变化；热、电与姿态响应仍由原生模型演化产生。其他类型事件使用各自的单位和参数，不能把这条资源损失规则推广成所有故障的通用线性机理。

已有显式参数 `remaining_capacity_ratio`、`remaining_efficiency_ratio`、`remaining_rejection_ratio`、`remaining_ratio`、`loss_fraction` 或 `loss_pct` 保留原有优先级，不被标量覆盖。没有给出标量和这些参数时，仍使用该效果既有默认值。非数值、布尔值、非有限数和区间外标量在资源损失解析阶段被拒绝。输入 TaskSpec 和模型原始回答不改写；派生比例记录在实际运行事件参数中。

## 容量突降、储能与 SOC 的边界

当前原生路径使用 SimpleBattery 的净功率积分与容量上限模型：容量以 J 表示，`battery_capacity_wh` 乘 3600 后作为容量上限；SOC 为模型储能除以当前有效容量。参数事件改变容量上限，不将已有储能按损失比例同步缩放。因此，容量突降可使 SOC 上升；若原有储能超过新上限，储能状态可能被上限裁剪，超出部分不再作为此模型的可用储能跟踪。

被裁掉的储能没有对应的故障功率或热源消息，也未自动计入热网络。当前模型不说明这部分能量在真实电池中如何保持、隔离或耗散；不能把储能状态的下降解释为等量瞬时发热。故障后持续处于满容量时，超过上限的净输入也不能据此追踪其物理去向。

`energy_bounds_pass` 检查已发布样本中的储能是否位于零与当前容量之间；`energy_balance_proxy` 检查所选代理量的非负性。两者均不是完整能量守恒残差，不能证明容量损失过程的电、热及其他能量去向闭合。本模型适用于声明假设下的可用容量和任务模式响应对照；真实故障耗散、热冲击和完整能量预算不在这条容量系数规则的验证范围内。

参考 [Basilisk SimpleBattery 官方说明](https://avslab.github.io/basilisk/Documentation/simulation/power/simpleBattery/simpleBattery.html)：该模块积分相连设备的净功率，并以容量限制储能。在线说明用于解释模型类别；某次运行的具体构建版本、参数、源码和遥测仍以其归档制品为准。

2026-10-29 的受控原生运行发现：旧解析器丢弃顶层幅度，`magnitude=0.1` 和 `0.3` 都执行为默认 50% 容量损失。失败记录位于 `reports/local_requirements_completion/native-event-magnitude-physical-repro/report.json`。修复后的真实 SPICE 原生运行分别得到剩余容量 90% 和 70%，显式 `loss_fraction=0.3` 对照也为 70%；记录位于 `native-event-magnitude-physical-fixed-file-entry/report.json`。运行前后物理源码均为 `84fa8bbd713f5b7aa80bd5095f658e70dedc042613feaa0448dcd79b63f10d82`。

37 项解析合同测试覆盖两种事件表示、三类资源损失、区间端点、非法幅度及显式参数优先级；额外原生测试仍须按效果和工况分别扩充。上述三次容量实测不构成所有损失机理的硬件验证。新源码的完整回归、软实时性能、热工况和同一实例真实 24 小时耐久分别验收，既有版本的通过记录不自动继承。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=verification_and_scoped_validation; phase=resource_loss_mapping_requalification; evidence=actual_native_capacity_mapping_verified; completion=unproven -->
