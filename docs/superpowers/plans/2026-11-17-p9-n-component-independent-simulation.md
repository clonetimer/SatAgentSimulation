# P9-N：单机级独立仿真

日期：2026-11-17
更新：2026-11-18

## 目标

让用户能在统一工作台中选择一个真实卫星单机，独立配置、独立执行有限时长仿真、查看时序曲线和参数/来源证据，并将故障能力严格限定在对应模型确实实现的范围内。

P9-N 是有限时长、单次运行的 component-level 仿真阶段。它复用现有 TaskSpec、Capability Registry、registered adapter、Run Bundle、结果与数据集管线；不新建第二套任务系统，也不把整星实时连续运行配置误用为单机实时运行。

## 当前代码事实

- TaskSpec 已支持 `simulation.level=component`，并已有独立 component capability 和场景模板。
- `component.reaction_wheel.v1` 已支持显式选择 `physics_backend=basilisk_native`。单轮采用官方 Basilisk Spacecraft + ReactionWheelStateEffector；3/4 轮也有独立 native cluster 路径。
- 原生 RW 当前只支持 nominal；不支持 native fault、degradation、constraint、时变 torque profile 或 EPS/ADCS 闭环。默认 backend 仍是 project_reference，不能把默认结果称为 Basilisk。
- `component.imu.v1`、`component.magnetometer.v1`、`component.star_tracker.v1` 默认仍为项目 reference，已支持 opt-in `physics_backend=basilisk_native` 独立传感器图；CSS 仅有标量光强 probe，尚未映射现有三轴方向输出合同。
- `subsystem.eps.unified_native.v1` 与 `EpsUnifiedNativeAdapter` 已存在；其同一 SimBase 可连接官方 SimpleSolarPanel、SimpleBattery、SimplePowerSink 与项目 PDU。本轮复用现有路径补充真实物理和封存证据。
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

**已合入 main：PR #169（2026-11-18）**

- 合并提交：`14b3dd0fa9f4697befd7a14090a976ad6ddd8089`。
- 四类官方 Basilisk 独立图已建立：IMU、StarTracker、Magnetometer、CoarseSunSensor；均使用显式 truth/environment 消息和官方 recorder。
- `component.imu.v1`、`component.magnetometer.v1`、`component.star_tracker.v1` 已接入 opt-in `physics_backend=basilisk_native` TaskSpec adapter；默认仍为 `project_reference`。
- 三类传感器均通过 `prepare_run → execute_prepared_run → sealed Run Bundle`，Validation PASS 与 SHA-256 integrity 验收。
- CSS 暂不接入 native TaskSpec：官方 CoarseSunSensor 只输出标量光强，现有 capability 还声明三轴方向估计；不复制输入太阳方向伪装测量输出。
- 合入时专项 CI：官方 Basilisk 2.11.0，26 passed；参数表审计 PASS。
- 不把传感器独立图描述为光学/地磁/轨道环境完整模型，也不声称飞行资格。

### P9-N3：EPS / 推进 / 热控等单机

**EPS 首批已合入 main：PR #171（2026-11-18）；推进器首批单机执行与封存验收已实现**

- 电池：官方 `SimpleBattery` + `SimplePowerSink` 已接入 `component.battery.v1` opt-in native TaskSpec adapter。
- 太阳翼：官方 `SimpleSolarPanel` 已接入 `component.solar_panel.v1` opt-in native TaskSpec adapter；输入为显式 SCStates/Sun/Eclipse 消息，输出为官方 `nodePowerOutMsg.netPower`。
- 已增加同一 SimBase 中 SimpleSolarPanel PowerNode → SimpleBattery 的耦合回归，验证真实发电使储能增加；不使用独立 Run 的 CSV 后处理冒充物理耦合。
- Native 边界：电池只支持 nominal、恒定净功率、unity efficiency、SOC [0,1]；太阳翼只支持一块完全展开固定板、静态 Sun/Eclipse、无 tracking。未实现工况 fail closed。
- PR #171 已合入 main，合并提交 `5f294149bfb637117b4fb7772ed6e1053b84f695`，替代已关闭的 PR #170。受测 head `b0745ef67f8520d1485a8f7162f09f844466bb82`：官方 Basilisk 2.11.0 专项 [26 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44245235664)，Agent/Run [457 passed、Chromium 19 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44245235691)，参数/ownership/脚本与依赖门禁成功；本地相关回归 55 passed，合并树与受测树一致。
- 已修正太阳翼能量摘要对实际 recorder 的积分、电池限幅能量核算和未支持输入的拒绝规则。时间网格校验与任务/recorder/stop 共同使用按 Basilisk 规则舍入的纳秒整数，避免浮点取整差异。新增反例在修复前失败、修复后通过。
- 复用已有 `EpsUnifiedNativeAdapter`，充电、放电两个 subsystem TaskSpec → sealed Run Bundle 均通过 validation PASS、integrity 与逐区间能量守恒；已核查实际负载、官方模块与项目 PDU 来源。
- 本轮环境为固定显式 Sun/Eclipse 输入，充电与放电是独立工况；不宣称轨道太阳历元、动态日蚀或运行中工况切换已验证。后续按真实输入与 recorder 证据扩展，再推进推进器、贮箱和热控设备。
- 推进器新增 opt-in 官方 ThrusterDynamicEffector + Spacecraft + FuelTank 单机夹具，支持一次连续点火与零点火；保留每周期指令含义。密集原生推力先积分、再按输出周期抽取，燃料消耗取原生质量差。多段脉冲、事件、不完整时间网格、超步数及可能耗尽燃料的请求预检拒绝。
- 推进器本地专项 63 passed，正式 TaskSpec 1.0.0 示例 SUCCEEDED / validation PASS / sealed / integrity，核验 201 个密集采样点。实际 CI / 合并状态以该功能 PR 最新 head 为准；不将这一首批实现扩展成 native 故障、任意脉冲或真实装机耦合已验收的声明。

### P9-N4：单机工作台产品闭环
- 对话框选择单机 → 参数配置 → 预检 → 执行 → 曲线/指标 → 历史 Run → 数据导出。
- 参数必须显示单位、范围、默认值、来源和实际 backend。
- 结果只显示真实输出字段；不将参数输入冒充测量值。
- 保留统一 TaskSpec 和 Run Bundle，后续可被分系统/整星模型复用。

## 当前验收状态

- P9-N0：完成，PR #165 已合并，合并提交 `655a9d009f2d065cf349f78ecc5fb5f53bbbd16f`。
- P9-N1：官方原生执行与 sealed Run Bundle 已 PASS；native 单机模板及 backend 表单契约测试 PASS。工作台端到端浏览器验收仍在收敛，不将单测 PASS 等同于产品闭环完成。
- P9-N2：PR #169 已合入 main；三类传感器原生 TaskSpec/Run Bundle 已通过，CSS 保持 probe-only，等待输出合同调整。
- P9-N3：电池、太阳翼 native component adapters、同 SimBase PowerNode 耦合与既有 EPS 分系统 sealed 验收已随 PR #171 合入。推进器首批一次连续点火、零点火和正式封存验收已实现；贮箱独立 native capability 与热控原生接入仍为后续工作。

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
- P9-O 分系统仿真后续复用 component capability 与物理接口；本轮只复用已有 EPS 分系统图补证据，不扩展任意跨设备或跨分系统闭环。
- 整星 UnifiedNativeRuntime 保持现有运行图，不因 component 单机能力而拆分或替换。
