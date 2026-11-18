# P9-N：单机级独立仿真

日期：2026-11-17

## 目标

让用户能在统一工作台中选择一个真实卫星单机，独立配置、独立执行有限时长仿真、查看时序曲线和参数/来源证据，并将故障能力严格限定在对应模型确实实现的范围内。

P9-N 是有限时长、单次运行的 component-level 仿真阶段。它复用现有 TaskSpec、Capability Registry、registered adapter、Run Bundle、结果与数据集管线；不新建第二套任务系统，也不把整星实时连续运行配置误用为单机实时运行。

## 当前代码事实

- TaskSpec 已支持 `simulation.level=component`，并已有独立 component capability 和场景模板。
- `component.reaction_wheel.v1` 已支持显式选择 `physics_backend=basilisk_native`。单轮采用官方 Basilisk Spacecraft + ReactionWheelStateEffector；3/4 轮也有独立 native cluster 路径。
- 原生 RW 当前只支持 nominal；不支持 native fault、degradation、constraint、时变 torque profile 或 EPS/ADCS 闭环。默认 backend 仍是 project_reference，不能把默认结果称为 Basilisk。
- `component.imu.v1` 与 `component.star_tracker.v1` 当前是确定性的项目 source-native 模型，不是独立 Basilisk 传感器运行图。整星 UnifiedNativeRuntime 内另有官方 Basilisk IMU、StarTracker、Magnetometer 和 CoarseSunSensor。
- `continuous_initial_config.py` 明确声明 continuous graph scope 为 integrated whole-native only，standalone subsystem continuous 和 arbitrary component continuous 当前均不支持。

## P9-N 阶段拆分

### P9-N0：能力盘点与运行契约

<badge>已完成并合入 main</badge>

已合入 PR #165，官方 Basilisk 2.11.0 的 RW 物理实验和 TaskSpec → sealed Run Bundle 集成验收通过。
- 盘点 component capabilities、backend 来源、参数、输出、故障模式、限制。
- 定义 component 单次仿真必须复用的 TaskSpec → validate/plan → Run Bundle → registered adapter → sealed result 端到端链。
- 加入真实 Basilisk RW component Run Bundle 集成测试，证明不仅 adapter 直调通过，正式任务执行路径也能运行并保留 native provenance。
- 明确 source-native project model 与 Basilisk-native backend 的 UI 标签，防止混淆。

### P9-N1：单机原生执行首例——反作用轮

<badge>核心执行链 PASS</badge>

已完成：官方 Basilisk RW recorder 经正式 TaskSpec / Run Bundle 执行，SUCCEEDED、validation PASS、sealed 和逐文件 SHA-256 完整性均通过；新增 `component_reaction_wheel_basilisk_native` 可选场景模板，参数表单将原生 backend 作为推荐模式可见选项。产品级浏览器体验与曲线验收继续纳入 N1 收尾。
- 以一个独立 RW 为首个工程单机；复用现有 `component.reaction_wheel.v1` 和 `physics_backend=basilisk_native`。
- 完成参数说明、输入约束、原生测量输出、sealed Run、结果曲线和 provenance。
- 对 native unsupported modes 做 fail-closed UI/validator 提示；不得静默退回 Python。
- 再验证 3/4 轮 cluster 是多个单机组成的设备组，不把它伪装成单轮。

### P9-N2：传感器单机

**实施启动（2026-11-18）**
- 已建立独立官方 Basilisk StarTracker probe graph：显式 `SCStatesMsg` truth fixture → 官方 `sensorOutMsg.qInrtl2Case` recorder。
- 已建立独立官方 Basilisk IMU probe：显式 `SCStatesMsg` 真值（角速度 + 非保守加速度）、零随机噪声、identity mounting、零杆臂 → 官方 `AngVelPlatform` 与 `AccelPlatform` recorder。
- 已把 `physics_backend=basilisk_native` 接入 `component.imu.v1` 的现有 adapter/TaskSpec 路径；可配置原有 gyro/accel truth、scale 和 bias，Run 结果保留 native provenance；fault/degradation 和非 nominal 工况 fail closed。
- 已把 `physics_backend=basilisk_native` 接入 `component.magnetometer.v1`；可配置磁场、bias 和统一三轴 scale；非均匀轴 scale、fault/degradation 和非 nominal 工况 fail closed。
- 已把 `physics_backend=basilisk_native` 接入 `component.star_tracker.v1`；官方 quaternion 转换为 capability 已声明的 MRP trace；`valid` 仅表示存在归一化 recorder sample，不表示光学星点可见。非零 drift modifier、fault/degradation 和非 nominal 工况 fail closed。
- CSS 保持 native probe，不开放 TaskSpec native backend：官方 CoarseSunSensor 只输出标量强度，而现有 capability 还声明三轴方向估计；在区分“传感器测量”与“上层方向解算”之前，不伪造方向输出。
- 已建立独立官方 Basilisk Magnetometer probe：显式 `SCStatesMsg` + `MagneticFieldMsg` → 官方 `tamDataOutMsg.tam_S` recorder；当前输入为均匀地磁场 fixture，不宣称地球磁场环境模型。
- 已建立独立官方 Basilisk CoarseSunSensor probe：显式 `SCStatesMsg` + `SpicePlanetStateMsg` + `EclipseMsg` → 官方 `cssDataOutMsg.OutputData` recorder；当前验证余弦/FOV 与照明输入，不宣称 SPICE 轨道历元或真实遮挡几何。
- 已加入聚焦 CI 工作流，固定官方上游 `bsk==2.11.0`，运行四类传感器原生图与 fail-closed 输入测试。
- 所有 probe 均独立构建，不裁剪整星 UnifiedNativeRuntime 输出；当前仍是物理试验接口，不冒充已注册生产 capability。

- IMU、星敏、磁强计、太阳敏感器逐个盘点。
- 先保留现有 project source-native capability 的真实标签与行为。
- 对官方 Basilisk 有原生模块的传感器，分别构建独立 Basilisk test graph：只提供必要的 truth/environment input，直接记录官方 sensor output；不可把整星 UnifiedNativeRuntime 的输出剪裁后宣称为独立运行。
- 无法构造可信独立输入条件的传感器，明确标注依赖输入/环境，不强行声称可 standalone。

### P9-N3：EPS / 推进 / 热控等单机
- 电池、太阳翼、推力器、贮箱、加热器等按各自 capability 和实际物理模型逐项接入。
- 区分 project source-native、Basilisk-native 和工程参考模型。
- 逐个完成 nominal、参数响应、边界条件和已实现故障/退化的证据测试。

### P9-N4：单机工作台产品闭环
- 对话框选择单机 → 参数配置 → 预检 → 执行 → 曲线/指标 → 历史 Run → 数据导出。
- 参数必须显示单位、范围、默认值、来源和实际 backend。
- 结果只显示真实输出字段；不将参数输入冒充测量值。
- 保留统一 TaskSpec 和 Run Bundle，后续可被分系统/整星模型复用。

## 当前验收状态

- P9-N0：完成，PR #165 已合并，合并提交 `655a9d009f2d065cf349f78ecc5fb5f53bbbd16f`。
- P9-N1：官方原生执行与 sealed Run Bundle 已 PASS；native 单机模板及 backend 表单契约测试 PASS。工作台端到端浏览器验收仍在收敛，不将单测 PASS 等同于产品闭环完成。
- P9-N2：待启动；优先 IMU 独立原生图，再评估星敏、磁强计和太阳敏感器的独立输入条件。

## 首阶段验收门槛

1. 单机任务以 `level=component` 编译，capability_id 与 target object 一致。
2. 真实 Basilisk RW Run Bundle 必须通过官方 Basilisk 2.11.0；记录实际 distribution provenance。
3. Run Bundle 状态 SUCCEEDED，validation PASS，sealed/integrity PASS。
4. trace 必须来自 Basilisk recorder：wheel speed、applied motor torque、friction torque；扭矩受原生 u_max 限制。
5. execution evidence 必须标记 registered capability adapter、非 legacy route、Basilisk-native provenance。
6. 通过项目参考模型的默认路径和 native opt-in 路径的差异测试；默认 project_reference 不得冒充 native。
7. Native unsupported mode、错误参数和 Basilisk 缺失必须失败关闭，不得回退。
8. 本阶段不宣称 native fault/degradation 已完成，不宣称单机实时连续仿真已支持。

## 依赖与边界

- P9-M4 工程几何审查仍独立推进。P9-N 可以先做无 CAD 依赖的物理单机实验；完成后再把经审查的 CAD/安装坐标接入单机视图。
- P9-O 分系统仿真后续复用 component capability 与物理接口；P9-N 不在此阶段实现跨设备闭环。
- 整星 UnifiedNativeRuntime 保持现有运行图，不因 component 单机能力而拆分或替换。
