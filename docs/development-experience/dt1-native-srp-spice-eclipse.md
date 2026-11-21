# DT1-A2：真实 SPICE/Eclipse/Cannonball SRP 原生整星耦合

日期：2026-11-20；对应 DT-0 调整 `CHG-DT0-009`、LEO-04。状态：候选代码，原生 CI A/B 未验收前不声明动力学通过。

## 实现与来源

- 复用官方 Basilisk 2.11.0 `Basilisk.simulation.radiationPressure.RadiationPressure` 的 cannonball 模式，不复制 SRP 公式。
- 仅在 `environment_profile=spice_wmm`、`srp_model=cannonball` 且明确提供 `srp_reference_area_m2` 和 `srp_coefficient_reflection` 时启用；默认 `disabled`，原有 R0 不变。
- 真实 `sunEphmInMsg` 直接连接已有 DE430 SPICE Sun 输出，`sunEclipseInMsg` 连接已有 Eclipse 输出，原生 `RadiationPressure` 通过 `spacecraft.addDynamicEffector` 纳入同一 SimBase/Process/Task。
- 显式启用时，仅调整当前 Task 内 Eclipse 和 SRP 的调度先后，确保它们在 spacecraft dynamics 之前更新 Sun 和遮挡；禁用时所有原有模型优先级保持原样。
- 力采用 RadiationPressure 的 `forceExternal_N` 惯性系，记录在 Run Trace；不能误读为 DragDynamicEffector 的 `forceExternal_B`。记录真实 SHA 已核验的 DE430/SPICE 资产和 Eclipse 消息来源。
- 20 m²、Cr=1.5、100 kg 仅用于 *测试夹具*；不代表真实航天器、BIRDS-4 的气动/光学面积、飞行标定或默认参数。

## 可证伪测试

`tests/test_native_srp_spice_eclipse.py` 根据真实 SPICE 太阳方位自动选择太阳侧/地球本影侧两种轨道初相位，不猜太阳历史序列，分别执行同初始条件 SRP OFF/ON 的整星 A/B。验收检查包括：两个实际模型消息连接、Sun 距离与单机输出力比例和方向、明区非零受力、遮挡区零受力、明区末端轨道位置差、暗区无虚构力；打印力/遮挡/位置差的实际数值并维持严格断言。

无 SPICE、未显式给出面积/系数、非法参数或非整星路径均失败关闭。不能把 CI 中单一受力非零替代任务级精度、STK/GMAT 独立工具比对或外部飞行数据校验。

## 保留缺口

- 无可信 as-built 表面面积/法向/光学系数，官方面片 `facetSRPDynamicEffector` 尚未接入，角度相关 SRP 与太阳翼铰接不在本阶段。
- `EarthRadiationModel` 反照、地球红外仍仅是环境模块候选，不声称已转化整星受力。
- 不执行非独立的 J3/J4、IGRF 模型晋升，不修改积分步长/旧默认值/既有验收阈值。
