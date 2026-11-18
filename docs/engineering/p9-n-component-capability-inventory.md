# P9-N 单机能力盘点（2026-11-17）

更新：2026-11-18

| 单机/设备 | 当前 capability / 入口 | 当前实际模型来源 | 当前可独立执行 | 当前边界 / P9-N 处理 |
|---|---|---|---|---|
| 反作用轮 | `component.reaction_wheel.v1` | 默认项目 source-native Python；显式 `physics_backend=basilisk_native` 时官方 Basilisk Spacecraft + ReactionWheelStateEffector | 是，有限时长 TaskSpec；1/3/4 轮 native 路径 | 1-wheel 原生 Run Bundle 已 PASS；新增 `component_reaction_wheel_basilisk_native` 工作台模板；Native 仅 nominal，无 native fault/degradation/constraint/profile |
| IMU | `component.imu.v1` | 默认项目 reference；opt-in 官方 Basilisk ImuSensor 独立图 | 是，两条有限时长 TaskSpec 路径；native sealed 验收已通过 | 显式 SCStates 角速度/非保守加速度 truth→AngVelPlatform + AccelPlatform recorder；nominal only，identity mounting、零杆臂/无随机噪声 |
| 星敏感器 | `component.star_tracker.v1` | 默认项目 reference；opt-in 官方 Basilisk StarTracker 独立图 | 是，两条有限时长 TaskSpec 路径；native sealed 验收已通过 | 官方 quaternion→MRP trace；valid 仅表示 recorder sample 有限且归一化，不含星表/光学成像/FOV 盲区场景 |
| 磁强计 | `component.magnetometer.v1` | 默认项目 reference；opt-in 官方 Basilisk Magnetometer 独立图 | 是，两条有限时长 TaskSpec 路径；native sealed 验收已通过 | 显式 SCStates + 均匀 MagneticFieldMsg→tam_S；统一 scale/nominal only，尚未接入地球磁场环境模型 |
| 太阳敏感器 | `component.sun_sensor.v1` | 项目 source-native；整星图中有官方 Basilisk CoarseSunSensor + Sun ephemeris | 是，项目模型可独立运行 | P9-N2 已建官方 CSS probe：显式 Sun/Eclipse/SCStates 消息→OutputData；暂不接入 TaskSpec native backend，因为现有 capability 还要求三轴方向估计而官方 CSS 只输出标量强度；尚未接入 SPICE 轨道历元和真实遮挡几何 |
| 电池 | `component.battery.v1` | 默认项目 reference；opt-in 官方 Basilisk SimpleBattery + SimplePowerSink | 是，有限时长 TaskSpec / sealed Run Bundle | nominal、恒定净功率、unity efficiency、SOC [0,1]；限幅能量按 recorder 净功率/储能差核算，不宣称真实分流电路 |
| 太阳翼 | `component.solar_panel.v1` | 默认项目 reference；opt-in 官方 Basilisk SimpleSolarPanel | 是，有限时长 TaskSpec / sealed Run Bundle | 单块完全展开固定板、静态 Sun/Eclipse、无 tracking；摘要能量按 recorder 积分；非法方向、数量、采样网格与未实现事件被拒绝 |
| EPS（既有分系统复用） | `subsystem.eps.unified_native.v1` / `EpsUnifiedNativeAdapter` | 同一 SimBase 的官方 SimpleSolarPanel、SimpleBattery、SimplePowerSink + 项目 PDU | 已有有限时长 subsystem TaskSpec 入口，PR #171 已合入 | 充电/放电、多负载、能量守恒 sealed 验收与官方 CI 已通过；固定显式 Sun/Eclipse 输入，不宣称轨道太阳历元、动态日蚀或工况切换已验收 |
| 推力器 | `component.thruster.v1` | 默认项目 reference；opt-in 官方 ThrusterDynamicEffector + Spacecraft + FuelTank 夹具 | 有限时长 component TaskSpec；native sealed 验收通过 | nominal、一个连续点火区间或零点火；保持每周期命令语义，密集推力积分与原生质量差；固定夹具不代表装机，多段脉冲/事件被拒绝 |
| 贮箱 | `component.fuel_tank.v1` | 独立入口仍为项目模型；推进器试验夹具有官方 FuelTank | 项目 component 可执行；独立 native 后端待实现 | 官方消息提供质量/质量流率，不提供现有合同中的压力；不得把项目压力模型冒充原生贮箱热力学 |
| 加热器 / 热节点 / 散热器 | 对应 component capabilities | 项目工程热模型 | 是，有限时长 TaskSpec | 不宣称 Basilisk 原生热控；P9-N3 |
| 其他通信、载荷、PDU 等 | 多个 component capabilities | 依 capability 声明为 source-native / project reference / proxy | 逐项可执行性不同 | 必须按真实 capability contract 核查，不作统一 native 宣称 |

## 关键运行架构事实

- `TaskSpec.SimulationSpec.level` 已支持 `component`。
- `scenario_templates.py` 已有单机模板，例如 `component_reaction_wheel_nominal`、`component_imu_nominal`、`component_star_tracker_nominal`。
- `RegisteredCapabilityExecutionAdapter` 将 capability 交给 registry 中的 adapter，Run Bundle 记录 execution evidence。
- EPS 已有分系统 adapter 与同一 SimBase 连接图；P9-N3 优先复用并核实模块、消息、recorder 和封存结果，不再规划重复 adapter。
- 现有 `continuous_initial_config.py` 的配置目录明确只服务整星 UnifiedNativeRuntime：`graph_scope=INTEGRATED_WHOLE_NATIVE_ONLY`、`standalone_subsystem_continuous_simulation_supported=false`、`arbitrary_component_types_supported=false`。这不否定有限时长 component TaskSpec 能力，但说明单机实时连续运行不能沿用现有整星 realtime API。

## 用户界面必须区分的三类来源

1. **Basilisk 官方原生**：真实调用 Basilisk 模块并记录原生 recorder 数据。
2. **项目 source-native**：项目维护的公开组件模型/API，可能是确定性模型或简化物理模型。
3. **工程参考/代理模型**：为任务链或系统级耦合提供的工程近似。

这三个标签必须在设备选择、参数配置、运行摘要和导出 Run Bundle 中保持一致。


## P9-N 实测结果（2026-11-17）

- 官方上游 `bsk==2.11.0` 安装与版本 provenance 检查 PASS。
- 原生 RW 单轮、三轮、四轮 SWIG 物理测试 PASS。
- 正式 component TaskSpec 经 registered capability adapter 执行并封存 Run Bundle：SUCCEEDED / validation PASS / integrity PASS。
- 原生 recorder 输出包括 wheel speed、physically applied motor torque 和 friction torque；实际施加力矩受原生硬件 `u_max` 限制。
- 新增原生反作用轮场景模板，默认使用 1-wheel nominal 和 Basilisk native backend；表单中 backend 字段属于 common 参数，在“推荐参数”模式可见，且明确区分项目参考模型与 Basilisk 原生模型。

## N2 / N3 状态补充（2026-11-18）

- N2：PR #169 已合入 main，合并提交 `14b3dd0fa9f4697befd7a14090a976ad6ddd8089`；官方 Basilisk 2.11.0 专项 26 tests 通过。IMU、磁强计、星敏为 opt-in native；CSS 仍仅为标量光强 probe。
- N3 EPS 首批：PR #171 已合入 main，合并提交 `5f294149bfb637117b4fb7772ed6e1053b84f695`，替代已关闭的 PR #170。最终受测 head `b0745ef67f8520d1485a8f7162f09f844466bb82` 的官方 Basilisk 2.11.0 专项 [26 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44245235664)，Agent/Run [457 passed、Chromium 19 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44245235691)，本地相关回归 55 passed；合并树与受测树一致。
- 物理输出以 recorder 为准：太阳翼能量不能来自解析期望值；电池限幅能量只是净功率/储能差的派生核算。零方向向量与不对齐的时长/采样网格应 fail closed，不能靠默认值或外推补齐。
- N3 推进器首批：官方 Basilisk 2.11.0 本地专项 79 passed（77 项新 native 测试 + 2 项既有 reference 事件测试），正式 TaskSpec 1.0.0 示例 SUCCEEDED / PASS / sealed / integrity。1 N、0.5 s 点火的记录积分约 0.5 N·s，原生质量减少约 0.000182092 kg；零点火和仅一个原生步长的点火均有验收。最短点火保留步长相关积分误差边界，不宣称任意脉冲精度或 native 故障已完成。
