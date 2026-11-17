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
