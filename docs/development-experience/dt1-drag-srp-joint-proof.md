# DT1-A3｜NRLMSISE-00 阻力与 SPICE/Eclipse SRP 联合整星证据

日期：2026-11-20。基于 #206 HEAD `ffecb139f1f9861a80e1ba6ff7d091eff66f7a30` 和 #208 HEAD `05e4f24fa7e79277bd72edaa5cf794de508ed0f6` 的**独立集成候选**。这是跨 PR 风险验收，不是合并批准，也不替代任一原 PR。

## 原因与边界
- 两个 PR 分别修改 `unified_native.py`、能力 YAML、参数审计、原生 CI，同一 `main` 基础上的单独 PASS 不证明可同时使用。
- 以 #206 原生运行图为基底，仅移植 #208 的显式 SRP 消息链；保留 #206 的 23 路空间天气、MsisAtmosphere、ZeroWindModel、DragDynamicEffector、CSV/SHA-256 资产验证与默认无阻力行为。
- 集成能力的已注册参数目标从 172 扩为 175（NRLMSISE 的三个 opt-in 加上 SRP 的三个 opt-in），均不改变任何历史参数默认值或物理模型常数。
- 后端直接读取原生 SRP 惯性系 `forceExternal_N` 与 drag 机体系 `forceExternal_B`，不能把两种分量错误相加或用 CAD 推导面积。
- 如果同时启用，唯一来源为现有真实 Basilisk SimBase/Process/Task，太阳来自 DE430、遮挡来自 Eclipse、密度来自官方 NRLMSISE-00、风仅代表地球共转。
- 这不是面片/姿态相关气动和光压，也不包含独立 GMAT/STK 相关、热层风或飞行精度标定。

## 联合物理试验
测试文件 `tests/test_native_drag_srp_joint.py`：

1. 在真实 SPICE Sun 数据基础上选择太阳照射方向。相同初始状态、仿真步长下分别跑 `OFF/OFF`、`drag ON/SRP OFF`、`drag OFF/SRP ON`、`ON/ON`。
2. 要求同一模块清单中同时存在 MsisAtmosphere、SpaceWeatherData、DragDynamicEffector、RadiationPressure，所有动态效应器实际接入 spacecraft；密度/两类力均非零，拖曳耗散功率为负，SRP 方向远离太阳。
3. 检查增加任一受力均在末端轨道状态产生可测差异；不假设数值积分结果能够严格线性叠加。确保有阻力时半长轴低于仅有 SRP 的基线。
4. 根据真实 Sun 方位把同一任务放入地球本影，断言 SRP 受抑制但大气阻力仍然非零，与只启用 Drag 的终态一致。
5. 使用与 #206 相同的 `test-fixture://celestrak-compatible-sample` 合成空间天气快照；测试参数质量 100 kg、drag 面积 1 m² / Cd 2.2、SRP 面积 20 m² / Cr 1.5 均仅为**测试输入**，不是默认参数、CAD 源或真实标定。

只有原生 CI 记录上述 A/B 数值以及原有回归全 PASS 后才能声明该候选组合通过，不能把已通过的两个独立 PR 当成联合证据。

## 经验
- 不通过修改 PR #206/#208 或让其中一个自动合并来制造“集成通过”假象，单独构建组合候选。
- 所有测试均读取真实输出字段和 Run Summary，避免复刻简化公式代替 Basilisk 动力学。
- 将跨 PR 冲突视为验证对象：组合参数/记录/消息源都必须共存且证据可追溯。

## 原生试验第一次反馈（Run 44770510610）

- `Verify native SPICE WMM, NRLMSISE-00 drag and orbit-step convergence`：12 PASS，空间天气 10 PASS，独立 SRP 9 PASS。
- 联合测试首轮 FAIL：`tests/test_native_drag_srp_joint.py:123` 使用 `min(dforce)>0`，把最初 `t=0` 的 DragDynamicEffector 尚未计算的初始化值 `0 N` 误算成运行中无阻力；后续已显示 `0.0030069314941317425 N`、`0.003014485860876135 N` 等连续非零力。
- 修复只在测试中明确初始化时间语义：必须 `t=0` 的该字段为 0，且 **后续全部至少 30 个采样点**的真实机体系 Drag 力均有限、严格大于零。未修改任一质量/面积/密度、求解步长、官方 Basilisk 动态模块或旧测试阈值。
- 其余联合力、功率、地影和四路轨道 A/B 条件没有改变，留待新 HEAD 实测判定。
