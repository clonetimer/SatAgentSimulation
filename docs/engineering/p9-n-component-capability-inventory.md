# P9-N 单机能力盘点（2026-11-17）

| 单机/设备 | 当前 capability / 入口 | 当前实际模型来源 | 当前可独立执行 | 当前边界 / P9-N 处理 |
|---|---|---|---|---|
| 反作用轮 | `component.reaction_wheel.v1` | 默认项目 source-native Python；显式 `physics_backend=basilisk_native` 时官方 Basilisk Spacecraft + ReactionWheelStateEffector | 是，有限时长 TaskSpec；1/3/4 轮 native 路径 | 1-wheel 原生 Run Bundle 已 PASS；新增 `component_reaction_wheel_basilisk_native` 工作台模板；Native 仅 nominal，无 native fault/degradation/constraint/profile |
| IMU | `component.imu.v1` | 项目确定性 scale/bias source-native；整星图中另有官方 Basilisk ImuSensor | 是，项目模型可独立运行 | 尚无独立 Basilisk IMU graph；P9-N2 单独构建 truth-driven sensor graph |
| 星敏感器 | `component.star_tracker.v1` | 项目确定性姿态/漂移 source-native；整星图中另有官方 Basilisk StarTracker | 是，项目模型可独立运行 | 当前无星表/光学场景仿真；P9-N2 验证官方模块独立输入条件 |
| 磁强计 | `component.magnetometer.v1` | 项目 source-native；整星图中有官方 Basilisk Magnetometer + 磁场环境 | 是，项目模型可独立运行 | 独立运行需明确地磁场输入与坐标系；P9-N2 |
| 太阳敏感器 | `component.sun_sensor.v1` | 项目 source-native；整星图中有官方 Basilisk CoarseSunSensor + Sun ephemeris | 是，项目模型可独立运行 | 独立运行需明确太阳矢量、遮挡/光照输入；P9-N2 |
| 电池 | `component.battery.v1` | 项目 Python 电池模型 | 是，有限时长 TaskSpec | 不等同于整星 EPS 原生闭环；P9-N3 |
| 太阳翼 | `component.solar_panel.v1` | 项目模型；整星 UnifiedNativeRuntime 有基于太阳方向的原生功率链路 | 是，按 capability 独立运行 | 需要明确光照/法线输入；P9-N3 |
| 推力器 / 贮箱 | 对应 component capabilities | 部分组件模型；整星推进有官方 Basilisk 模块与真实轨道/燃料响应证据 | 按 capability 分别可执行 | 单机执行与推进—轨道耦合不是同一验证；P9-N3 |
| 加热器 / 热节点 / 散热器 | 对应 component capabilities | 项目工程热模型 | 是，有限时长 TaskSpec | 不宣称 Basilisk 原生热控；P9-N3 |
| 其他通信、载荷、PDU 等 | 多个 component capabilities | 依 capability 声明为 source-native / project reference / proxy | 逐项可执行性不同 | 必须按真实 capability contract 核查，不作统一 native 宣称 |

## 关键运行架构事实

- `TaskSpec.SimulationSpec.level` 已支持 `component`。
- `scenario_templates.py` 已有单机模板，例如 `component_reaction_wheel_nominal`、`component_imu_nominal`、`component_star_tracker_nominal`。
- `RegisteredCapabilityExecutionAdapter` 将 capability 交给 registry 中的 adapter，Run Bundle 记录 execution evidence。
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


## P9-N1 status update (2026-11-17)

- P9-N0 has been merged: the official Basilisk 2.11.0 reaction-wheel component now passes the complete TaskSpec → registered adapter → Run Bundle → validation/seal/integrity path.
- The product-visible guided entry is intentionally narrower than the general component catalogue: it exposes only `component.reaction_wheel` as the first independently verified Basilisk-native device.
- The dedicated entry applies `component_reaction_wheel_basilisk_native` and fixes the backend to `basilisk_native` in its starting TaskSpec. The field remains editable, with the project-reference alternative explicitly labelled as non-Basilisk; changing it changes the model provenance.
- Only finite offline execution is exposed in this guided native-device flow. Standalone component real-time and continuous sessions remain unsupported.
- Other component capabilities remain available through their existing general workflows and retain their actual source/backend labels; they are not silently promoted into the verified-native device list.
