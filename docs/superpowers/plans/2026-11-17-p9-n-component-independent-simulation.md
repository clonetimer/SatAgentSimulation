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
- IMU、星敏、磁强计、太阳敏感器逐个盘点。
- 先保留现有 project source-native capability 的真实标签与行为。
- 对官方 Basilisk 有原生模块的传感器，分别构建独立 Basilisk test graph：只提供必要的 truth/environment input，直接记录官方 sensor output；不可把整星 UnifiedNativeRuntime 的输出剪裁后宣称为独立运行。
- 无法构造可信独立输入条件的传感器，明确标注依赖输入/环境，不强行声称可 standalone。

### P9-N3：EPS / 推进 / 热控等单机
- 电池、太阳翼、推力器、贮箱、加热器等按各自 capability 和实际物理模型逐项接入。
- 区分 project source-native、Basilisk-native 和工程参考模型。
- 逐个完成 nominal、参数响应、边界条件和已实现故障/退化的证据测试。

**实施启动（2026-11-18）**
- 已将官方 Basilisk `SimpleBattery` + `SimplePowerSink` 组件图接入 `component.battery.v1` TaskSpec adapter。
- 已将官方 Basilisk `SimpleSolarPanel` 固定板模型接入 `component.solar_panel.v1` TaskSpec adapter，显式输入航天器状态、太阳位置和日蚀消息，输出官方 `netPower`。
- Native backend 仅支持 nominal、恒定净功率、unity charge/discharge efficiency、SOC [0,1]；故障/退化、时变功率曲线、非 unity 效率均 fail closed。
- 输出映射到现有 SOC、storage、capacity 和 net-power trace；使用正式 Run Bundle 执行、validation 与完整性校验。
- 专项 CI 固定官方 Basilisk 2.11.0，3 passed；参数表审计 PASS。
- 已补充同一 SimBase 中 SimpleSolarPanel `nodePowerOutMsg` → SimpleBattery PowerNode 的耦合回归，验证太阳翼实际发电使电池储能增加；这不是两个独立 Run 的后处理拼接。
- 下一步：将该闭环纳入 EPS subsystem TaskSpec adapter，补齐多负载/日蚀工况和 Run Bundle provenance。

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
