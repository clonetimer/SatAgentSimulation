# P9-N 单机运行开发经验

日期：2026-11-17

## 1. 有限时长 component TaskSpec 与整星 continuous runtime 是两条不同能力

`TaskSpec.SimulationSpec.level` 已支持 `component`，并且 Capability Registry 有多个可执行的 component capability。不要因为 `continuous_initial_config.py` 声明 `standalone_subsystem_continuous_simulation_supported=false`，就误判有限时长单机任务也不支持；反过来也不能因为 component TaskSpec 能运行，就宣称单机实时连续仿真已经支持。

- 单机有限时长：TaskSpec → Capability Registry → registered adapter → Run Bundle → sealed result。
- 当前整星实时连续：`UnifiedNativeRuntime` integrated whole-spacecraft graph。
- P9-N 首阶段只交付有限时长单机任务；单机 continuous 需要独立 runtime lifecycle、pause/resume、telemetry session 和 recovery 设计，留待单独阶段。

## 2. Capability 名称不等于物理 backend

`component.reaction_wheel.v1` 默认使用项目 source-native Python wheel reference model；只有显式设置 `parameters.values.physics_backend=basilisk_native` 时，才调用官方 Basilisk Spacecraft + ReactionWheelStateEffector。

UI、Run summary、trace labels、execution evidence 和数据集必须保留真实 backend 标识。不得将默认 project_reference 结果写成 Basilisk 原生仿真。

## 3. 已有 adapter 单测不等于产品运行链验收

既有 native RW 测试直接调用 `ReactionWheelAdapter.run()`，能够证明组件适配器能执行原生模型，但没有覆盖完整 TaskSpec 编译、ExecutionPlan、隔离执行、Run Bundle sealing 和 artifact integrity。

P9-N 增加的端到端验收从 `instantiate_scenario_template` 构造正式 component TaskSpec，显式选择 Basilisk backend，通过 `prepare_run` / `execute_prepared_run` 执行，并核查：
- SUCCEEDED + validation PASS + sealed；
- native recorder 的 wheel speed 与 physically applied torque；
- 原生硬件 u_max 饱和；
- registered capability adapter / 非 legacy route；
- Basilisk model provenance；
- Run Bundle manifest、seal 与逐文件 SHA-256 完整性。

## 4. Native RW 当前边界必须在产品层显式呈现

当前官方 Basilisk RW component backend 支持 1、3、4 轮 nominal；不支持 native fault、degradation、constraint、time-varying torque profile 或 EPS/ADCS closed loop。Native 失败必须 fail closed，不能回退到 Python reference model。

3/4 轮 cluster 是多个轮体组成的单机组/执行机构组合，不等同于单个 RW 的 1-wheel 设备实例。单机产品体验应先以 1-wheel 为主案例，再将 cluster 作为扩展构型。

## 5. 传感器单机不能把整星图输出剪裁后冒充独立仿真

IMU、StarTracker、Magnetometer、CoarseSunSensor 在整星 UnifiedNativeRuntime 中已有官方 Basilisk 模块，但独立传感器运行需要显式定义 truth/environment input、采样周期、安装坐标、随机种子和原生 recorder。不能把整星运行图中的传感器输出切片后标记为 standalone component run。

先保留当前 component source-native capability，并在 P9-N2 分别建立独立官方 Basilisk sensor graph；若环境输入不完整，结果应标记为输入受限，不伪造光学/星表/地磁环境。


## P9-N 实测补充：从物理探针到工作台入口

2026-11-17，PR #165 合入后，新增可直接从场景模板目录选择的 `component_reaction_wheel_basilisk_native`。该模板固定为单轮标称原生执行，显式设置 `physics_backend=basilisk_native`，并提供可审计的初始转速、轮惯量、最大力矩、转速上限、指令力矩和阻尼。`physics_backend` 已在表单中标记为 common，因此默认“推荐参数”视图可见，不必切换到“全部参数”。

验收证据：
- 原生 RW 物理 A/B 工作流：PASS；
- TaskSpec → ExecutionPlan → registered adapter → sealed Run Bundle：PASS；
- 模板规范化与 TaskSpec 校验：PASS；
- 原生 backend 参数表单可见性与中文说明：PASS。

这意味着 P9-N1 不需要再另建单机任务系统；应继续复用现有仿真向导、模板选择、参数表单、任务中心和结果分析。下一步重点是浏览器端从选择反作用轮对象到运行原生模板、查看真实曲线和历史 Run 的完整用户旅程。


## P9-N2 快速推进记录（2026-11-18）

- 新增 `sat_sim.bsk_engine.native_sensor_component.run_native_star_tracker_probe`，独立运行官方 `Basilisk.simulation.starTracker.StarTracker`。
- Probe 输入为明确构造的 `SCStatesMsg` 真值消息（姿态 MRP、角速度、位置、速度），不依赖整星 UnifiedNativeRuntime。
- 输出只使用官方 `sensorOutMsg` recorder 的 `qInrtl2Case` 四元数；不假设未在当前工程契约中确认的输出字段。
- 通过四元数归一性、有限性、采样长度和输入参数 fail-closed 检查。
- 证据范围不包含星表、光学成像、质心提取、遮挡/FOV 可见性或星敏 blinding 场景；这些仍需独立环境模型。
- 原则：N1 浏览器历史导航问题不再阻塞 N2 物理图建设；N1 只保留必要产品闭环验收，N2/N3 按独立物理输入与 recorder 证据并行推进。


## P9-N2 四类传感器原生图（2026-11-18）

- **StarTracker**：官方模块接收显式 SCStates 姿态真值，直接记录 `qInrtl2Case`。不将其描述为光学星敏仿真。
- **IMU**：官方 ImuSensor 接收显式 SCStates 真值；当前先验证 identity mounting、零杆臂、零噪声条件下的 `AngVelPlatform`。加速度计输出需要明确比力来源，不能把位置/速度常量误当加速度真值。
- **Magnetometer**：官方 Magnetometer 接收显式 MagneticFieldMsg 和 SCStates；当前使用均匀场输入验证 sensor-frame 输出。地磁场模型、轨道/姿态变化另行加入。
- **CoarseSunSensor**：官方 CSS 接收 Sun ephemeris message 和 Eclipse message；当前验证照明和余弦/FOV 响应。真实 SPICE 太阳历元、地球遮挡和轨道环境另行加入。
- 四类传感器的共同规则：真实官方模块、显式输入消息、原生输出 recorder、独立模型图；不得从整星 Run Bundle 裁剪字段后宣称单机独立执行。
- 为避免 N1 浏览器测试阻塞物理主线，N2 使用独立轻量 CI workflow，固定 `bsk==2.11.0`，只运行原生传感器专项与输入边界测试。


### P9-N2 → IMU 首个生产执行入口

- `component.imu.v1` 增加显式 `physics_backend`，默认仍为 `project_reference`；只有用户明确选择 `basilisk_native` 才执行官方 Basilisk ImuSensor。
- Native adapter 复用原 capability、表单和 Run Bundle，不新建第二条仿真任务体系。
- 原始真值角速度/非保守加速度、scale 和 bias 进入官方模块；输出由 `AngVelPlatform` 与 `AccelPlatform` recorder 产生。
- Native 目前只支持 nominal；fault/degradation、非 identity mounting、非零杆臂和随机噪声试验 fail closed，不能回退到 Python reference。
- 验收分层：probe 证明官方模块输入/输出；adapter 测试证明 TaskSpec 路由和 backend provenance；后续正式 Run Bundle 浏览器旅程才证明产品级闭环。


### N2 原生 IMU 初始化帧与参数表审计经验

- Basilisk 2.11 的 IMU 第一个输出帧为零初始化帧；不能把它当作真实测量参与 truth A/B。Probe 明确丢弃该帧，并在 trace provenance 中标记这一处理。
- IMU 的 `AngVelPlatform` 与 `AccelPlatform` 是官方输出消息字段；角速度与非保守加速度必须分别用 SCStates 真值验证，不能只测 gyro 就宣称 IMU 完成。
- 新增 capability 参数必须同步更新 24 类组件参量字典及其 source contract blob；否则动态表单构建会以 `COMPONENT_PARAMETER_NAME_NOT_REVIEWED` fail closed。
- 对原生后端增加新参数时，先复用已审定的中文名称“物理计算后端”，避免产生未经审核的字段名。
- Magnetometer 官方组件当前暴露统一 scale factor；项目 reference 支持三轴 scale，因此 native adapter 对非均匀 scale 显式拒绝，不能把三个不同轴参数压成一个值。


### P9-N2 星敏与 CSS 的输出契约边界

- StarTracker 官方输出为四元数 `qInrtl2Case`；Basilisk 自带测试用 `MRP2EP` 作为期望、再由 `EP2MRP` 还原验证。因此当前适配器将官方四元数转换为现有 MRP trace，不使用项目参考模型的姿态方程。
- StarTracker 的 `valid` 字段在 native backend 中只表示“官方 recorder 产生了有限、归一化的姿态测量样本”；不能解释为星点可见、星表匹配或光学有效。
- CoarseSunSensor 官方输出是单个光强度量；项目当前 capability 还声明了三轴太阳方向估计。已决定暂不把输入太阳方向复制成输出估计，先保持 probe-only，待拆分测量量与上层解算量后再开放。
- 物理接口不能完全映射到既有 capability 时，正确动作是缩小可用范围或调整合同，不是填入“看起来合理”的假输出。
