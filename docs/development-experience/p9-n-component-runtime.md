# P9-N 单机运行开发经验

日期：2026-11-17
更新：2026-11-18

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

P9-N2 保留默认 component reference 路径，并已为 IMU、磁强计、星敏接入 opt-in 官方 Basilisk 独立图；CSS 保持 probe-only。若环境输入不完整，结果应标记为输入受限，不伪造光学/星表/地磁环境。


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


### N2 Run Bundle 端到端验收扩展

- 传感器 adapter 单测只能证明路由正确，不能替代 Run Bundle 验收。
- IMU、磁强计、星敏均已通过真实原生 TaskSpec Run Bundle 测试：prepare → official Basilisk execute → validation PASS → sealed manifest → integrity verification。
- 这条测试复用现有 `prepare_run` / `execute_prepared_run`，不建立第二套执行器或封存格式。
- PR #169 已合入 main，合并提交 `14b3dd0fa9f4697befd7a14090a976ad6ddd8089`；合入时官方 Basilisk 2.11.0 专项 26 tests 通过。CSS 在输出契约调整前保持 probe-only，标量光强不能冒充三轴方向测量。


## P9-N3 官方 Basilisk EPS 组件与耦合经验（2026-11-18）

- 电池 native backend 复用官方 `SimpleBattery` + `SimplePowerSink`；太阳翼 native backend 复用官方 `SimpleSolarPanel`。
- 独立组件 Run Bundle 通过后，增加同一 SimBase 的 PowerNode 连接测试：SimpleSolarPanel `nodePowerOutMsg` 直接接入 SimpleBattery，不使用两个 Run 的 CSV 后处理冒充闭环。
- 电池仅支持 nominal、恒定净功率、unity charge/discharge efficiency、SOC [0,1]；太阳翼仅支持单块固定、完全展开、静态 Sun/Eclipse、无 tracking。超出能力范围 fail closed。
- 太阳翼输出必须读取官方 `nodePowerOutMsg.netPower`；电池存储/SOC 必须读取官方 SimpleBattery recorder。
- `subsystem.eps.unified_native.v1` / `EpsUnifiedNativeAdapter` 原已存在，调用 `run_eps_basilisk_scenario`；同一 SimBase 内已有官方 SimpleSolarPanel、SimpleBattery、SimplePowerSink 与项目 `PduLoadSheddingSysModel`。应先核对并复用这条路径，再补充正式 Run Bundle 证据。
- 已通过既有分系统入口的充电、放电、多负载与逐区间能量守恒 sealed 验收，明确检查模块实例、recorder 与来源边界。PDU 是项目 SysModel，不是官方 Basilisk C++ PDU。
- 本轮使用固定显式 SCStates/Sun/Eclipse 输入；充电、放电分别设定工况，不等于轨道环境、动态日蚀或运行中充放电切换已验收。

### N3 摘要数据与输入边界评审经验

- 太阳翼正式 Run 最初为 `INCONCLUSIVE`，原因是声明的四个 summary QoI 缺失：`eps.solar.nominal_max_power_w`、`eps.solar.effective_max_power_w`、`eps.solar.nominal_efficiency`、`eps.solar.effective_efficiency`。修复应补齐真实输出合同，不能放松 validator。
- 分析期望值只用于校核。标称测试中官方 recorder 约为 44.80249 W，解析校核值为 45 W；生成能量必须按 recorder 的功率和实际时间间隔积分，不能使用 `expected_power_w × duration` 替代测量。
- 电池达到储能上限后的限幅能量，应由记录的净功率积分与实际储能增量之差核算。既有 `shunt_dissipated_wh` 字段必须说明其派生语义；它不证明已模拟真实分流电路或热耗散。相关修复与回归已通过本地官方 Basilisk 验证。
- 零太阳方向/板法向量必须显式拒绝，不能静默替换成 +X；持续时长与采样间隔不对齐时也应拒绝，避免漏记末段后仍按完整时长给出能量。未支持的 modifiers 和非有限参数同样不能被忽略。
- PR #171（`feat/p9-n3-native-eps-rebased-20261118`）替代已关闭的 PR #170。既有提交 `5a17a3d` 的官方 Basilisk 2.11.0 专项 7 tests 与适用门禁已绿，但评审仍发现摘要能量和输入处理问题；绿色测试只覆盖已检查的合同。
- PR #171 已合入 main，合并提交 `5f294149bfb637117b4fb7772ed6e1053b84f695`。最终受测 head 为 `b0745ef67f8520d1485a8f7162f09f844466bb82`，合并树与受测树一致。官方 Basilisk 2.11.0 专项 [26 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44245235664)；Agent/Run 门禁 [457 passed、Chromium 19 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44245235691)；参数审计、跨平台 ownership 和脚本/依赖治理门禁均成功。stable continuous 工作流被跳过，不能当作长时稳定运行证据。
- 本地扩大到受影响的组件工厂、太阳翼方向/tracking 与导出脚本回归后共 55 passed。网格回归覆盖合法 `10.01 / 1.001` 秒配置和纳秒舍入后不整除的反例；`macros.sec2nano` 使用 `int(seconds * 1e9 + 0.5)`，校验与任务、recorder、stop 必须共用同一组整数，不能各自进行浮点截断。

### N3 本次应持续复用的开发方法

1. 先查现有 capability、adapter 和 builder，再补缺失的执行链证据。已有 EPS 分系统图能够复用，不因工作项名称再造一套入口。
2. 为配置、指令、原生测量和派生统计分别记录来源。`currentNetPower` 必须来自原生消息；能量和冲量则由实际 recorder 时间积分。解析公式只作为独立校核，不能写回结果冒充观测。
3. 测试要检查物理响应和拒绝路径。初始 7 tests 通过后仍发现真实缺陷，说明绿灯只证明已覆盖的合同；新增回归应先暴露缺陷，再修复，不能通过放松 validator 获得 PASS。
4. 在合并时核对最新提交与 Actions 对应关系，再核对合并树。旧 head 的绿灯、跳过的 workflow 或仅有 adapter 单测都不能替代当前 Run Bundle 的封存与完整性证据。
5. 为下一种单机保留原有参数语义。推进器已有 `on_time_s` 表示每个采样周期的点火命令，不能直接改写为一次整段点火；只有语义等价的配置才可编译到目前可用的原生图。

## P9-N3 原生推进器单机接入经验（2026-11-18）

`component.thruster.v1` 增加 opt-in `physics_backend=basilisk_native`，复用既有推进器工厂与小型推进图。默认 `project_reference` 及其故障/退化路径保留。正式输入示例为 `examples/component_thruster_basilisk_native.yaml`，设计与验收范围见 `docs/superpowers/specs/2026-11-18-p9-n3-native-thruster-design.md`。

1. **官方效应器需要正确的动力学边界。** 裸 `ThrusterDynamicEffector` 不具备独立积分器所需的状态链接。单机试验使用一台官方效应器、Spacecraft 和 FuelTank；10 kg 自由载体、1 kg 初始推进剂、2 kg 贮箱容量、+X 方向和零力臂均为显式夹具参数，不是实际装机或 CAD 证据。
2. **保留周期命令语义。** 标量 `on_time_s` 仍是每周期命令，profile 仍沿用最后一项。首批只编译一个连续点火区间或全零指令；多段间断脉冲、重复的部分周期点火、非标称事件与不完整网格均明确拒绝。工作台原有“累计点火时间”标签也改为“每周期点火时间”。
3. **命令执行顺序影响可观测性。** 既有图的延迟发布器位于效应器之后，5 ms 点火可能使质量变化却不留下正推力样本。单机入口禁用该低优先级延迟发布器，复用相同控制器和消息，以更高优先级单次发布命令；其他分系统调用者的调度保持原样。
4. **先对密集原生记录积分，再抽取输出。** `native_step_s` 控制物理与 recorder 步长，`simulation.sample_s` 保持指令周期和输出周期含义。5 ms 点火完全位于 100 ms 输出点之间时，导出的推力点可以全为零，但密集记录给出的冲量仍为 0.005 N·s。不能把稀疏曲线重新积分后宣称没有点火。
5. **临界燃料请求需要积分余量。** 原生实测中，理想耗油量为初始库存 95% 的单步点火已触发内部燃料耗尽，而最终 FuelTankMsg 仍为正质量。预检因此按点火时长加一个原生步长预留燃料，不能只依赖执行后的质量符号。另行限制原生步数、纳秒精度、力平方和排气速度的可表示范围。
6. **字段来源要分别说明。** 实际推力来自 `THROutputMsg.thrustForce`；冲量由该密集记录按真实时间积分；耗油量来自 `FuelTankMsg.fuelMass` 的质量差。`on_time_s` 是指令，`effective_isp_s` 是配置参数，不冒充测量。零点火是有效成功对照，不沿用旧分系统 runner 对“必须有耗油和速度增量”的成功判据。
7. **有限数值不等于可分辨的测量。** 独立复审用 1e-10 N / 1 ms 和 1e-9 N / 0.1 ms 的真实点火发现：总冲量为正，单步质量递减却被 1 kg 贮箱状态的浮点舍入抹去。原生入口需要同时预检累积舍入预算，并在每个密集时间点检查质量差与推力积分的守恒残差；不能仅检查 finite、nonnegative 后把零耗油判为成功。
8. **预检与直接执行必须共享身份边界。** 原生执行入口也要验证 capability、任务类型和目标设备，避免调用者绕过 prepare 阶段后把错误目标执行成推力器。回归应证明错误请求根本不会进入原生图构建，而不仅检查 validate 返回了错误。
9. **同一积分步长不能存在相互矛盾的已消费输入。** Canonical `simulation.step_s` 会进入 runtime 的 `simulation.solver.step_s`；新增 `native_step_s` 时必须核对这条归一化路径。原生实测中，同一个 20 ms 点火在 5 ms 与 20 ms 步长下的记录耗油量不同，因此标准步长被忽略会造成实际结果与界面说明不一致。两项存在时必须在纳秒归一化后相同；旧 flat spec 没有标准/solver 步长时，直接执行和正式迁移都应保留有效原生步长，不能在迁移时填入采样周期而引入新冲突。这条默认路径也要经过 prepare/execute/封存测试。参数消费清单本身不能证明调度真的采用了该值。另一个约 50 天单步反例表明，整数纳秒转回秒后仍需核对往返一致性，不能只限制浮点值大小。

官方 Basilisk 2.11.0 的本地验收结果：77 项 native 组件测试与 2 项既有 reference 事件测试共 79 passed；参数表审计另有 4 passed，24 类组件的 source blob、字段、单位及名称绑定全部通过。正式 TaskSpec 1.0.0 示例经 prepare/execute 后为 SUCCEEDED、validation PASS、sealed、integrity PASS，核验 201 个密集原生采样点并导出 11 行。

[PR #172](https://github.com/clonetimer/SatAgentSimulation/pull/172) 已于 2026-11-18 合入 main，合并提交 `2cd59c2f7a494e33c70de6fff6d4cb7a6df23e99`。最终受测 head 为 `f1c65193627f41fea638a650e3859062b40bbe3b`；独立复审、CI 与合并后核查的树均为 `71be8d4c43b475e3db4e58ede6caeb11cbddc3b3`。该 head 的官方推进器专项 [79 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44254506719)，原生传感器 [26 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44254506630)，Agent/Run [457 passed、Chromium 19 passed](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44254506614)。参数表审计、Windows/Ubuntu/source-snapshot ownership、脚本与依赖治理均成功；stable continuous 被跳过，不作长时运行证据。最终复审中发现的低流量分辨率、直接执行身份校验、标准步长冲突和 legacy 默认迁移问题均已修复并在此 head 复验。

| 工况 | 记录推力积分 / N·s | 原生记录质量减少 / kg |
|---|---:|---:|
| 1 N、280 s 比冲、0.5 s 点火 | 0.5000000000000003 | 0.0001820921808892706 |
| 2 N、280 s 比冲、0.5 s 点火 | 1.0000000000000007 | 0.0003641843617785412 |
| 1 N、140 s 比冲、0.5 s 点火 | 0.5000000000000003 | 0.0003641843617785412 |
| 1 N、280 s 比冲、0.005 s 点火 | 0.005 | 0.000002427895745227282 |
| 零点火对照 | 0 | 0 |

步长投影修正另通过 121 项既有 TaskSpec 兼容性、canonical transport 与 optional-parameter refinement 测试。更广的四文件检查为 129 passed、1 failed；失败项 `test_recorded_actual_ai_canonical_export_is_identity_without_edits` 是整星固定计划哈希失配。对修正代码、初始 PR head `7259b61` 及本次开发前主分支 `5f294149` 的独立 Git worktree 执行同一测试，均得到相同 expected `d7499066…` / actual `02fa1aa6…`。该问题在本 PR 前已存在，固定期望保持不变。排除回归要回到 PR 主分支基线，仅与功能分支的较早提交比较不足以证明与本 PR 无关。

最短单步点火的耗油量与理想公式有约三分之一个原生步长的开关积分差异，验收采用步长相关的误差界；这不能作为短脉冲高精度证明。原生故障、多段遥控回放、安装几何和任务级推进耦合仍须分别扩展与验证。

## P9-N3 贮箱与后端合同经验（2026-11-18）

`component.fuel_tank.v1` 的原生后端需要解决可观测量差异：官方 `FuelTankMsg` 有质量、带符号质量导数和最大质量，没有压力。默认项目模型继续提供工程压力估算，原生任务则通过 `backend_profiles` 明确选择参数和输出。无需增加第二个贮箱 capability，也不改变现有 24 类对象的身份。

1. **先核对真实消息接口。** 官方 2.11.0 的输入是 `fuelLeakRateInMsg`，消息为 `MassFlowRateMsg.massFlowRate`；不是名字看似合理但不存在的 `massFlowRateInMsg`。连接消息会优先于 setter。这里把它声明为恒定质量排出边界，不宣称压力驱动供给或推力器耦合。
2. **后端合同必须贯穿默认值和封存。** 仅在结果里删除压力仍会让默认 QoI 要求末时压力。需要让规划、参数消费、编译、表单和 Bundle 合同快照共同选择有效合同，保留原来的严格结果验证。输出合同按节替换，避免深合并把参考字段带回原生模式。
3. **表单显示正确不代表提交正确。** 实测发现 `normalize_form_task_spec` 还会从基础 capability 补齐默认参数，重新塞入已从原生表单移除的满箱/干箱压力。默认值补全也要先选择后端；回归必须从表单归一化一直走到正式计划和封存。
4. **参数被登记不等于执行了参数。** 原生入口拒绝显式压力参数，即便它等于参考默认值；未执行的 canonical parameter overrides 也不能记录为已消费。选择后端按实际 runtime values，不能让兼容性的 model.config 改写权威 values。
5. **字典审计与当前可选曲线分工不同。** 中文量字典审查全部已声明后端的字段并集，重复字段的单位/类型必须一致；表单与执行只使用当前后端的字段集。不能为了通过审计而让参考模型承诺原生专属观测，或反向伪造原生压力。
6. **原生模块不会替产品完成所有物理预检。** 官方探针接受过初始 4 kg、容量 3 kg 的输入，因此入口显式约束初始质量不超过容量。首批拒绝可能耗尽的工况，并预留一个积分步；零流量和空箱零流量是有效成功对照。
7. **每个密集记录点都需要守恒证据。** 质量、流率和容量直接导出原生 recorder；耗油只用记录端点差。`m0-flow*t` 只作独立守恒检查。检测稀疏导出点之间的异常，防止两个输出点正常掩盖中间记录错误。对正流量还预检累计舍入预算，拒绝正命令但不可分辨的质量变化。
8. **切换后端也会改变已校验的配置。** 保留共用参数，移除不适用参数/输出并显示明细；同时更新表单 Schema、清除旧执行计划、向导校验与场景配置。载入原生模板和撤销表单投影时也要同步 Schema，不能只修改参数值。
9. **完整迁移链也必须保留拒绝条件。** 独立复审发现 legacy 迁移会漏掉显式顶层步长，以及某些矛盾任务身份、alternate events、top-level constraints 和未执行的 overrides。它们在 direct adapter 被拒绝，经过迁移却可能变成合法标称任务。现已在 native fuel 入口迁移前拒绝这些输入，并以正式封存检查 0.3 s 时长、0.1 s 步长确实产生四个原生记录点。不能只检查转换后的默认字段。
10. **原生内部中间量也有数值边界。** 初始质量与排出流率各自有限，并不能保证官方模块中的 mass×flow 可表示。实测正溢出会产生 NaN，零或次正规下溢会使正流率下的质量不变或失守恒。预检因此检查从初始质量到带一步余量的剩余质量之间，mass×flow 始终有限且为正规数；保留真实模块的接近上界成功对照及执行后的守恒复核。首批调度时间另明确限制为小于 2**52 ns，并保持 TaskSpec 原始时间顺序约束。
11. **独立夹具不能悄悄满足任务级耦合要求。** 正式规划本来会拒绝缺少的物理连接，direct adapter 也必须在构图前拒绝非空 mission.required_couplings，避免同一请求走不同入口得到不同的成功判据。

官方 bsk/Basilisk 2.11.0，1 s 时长、0.01 s 积分、0.1 s 导出的实际结果如下，每例含 101 个密集记录点、11 行导出：

| 初始质量 / kg | 容量 / kg | 排出命令 / kg/s | 原生末时质量 / kg | 原生质量导数 / kg/s |
|---:|---:|---:|---:|---:|
| 2 | 3 | 0.001 | 1.9989999999999934 | -0.001 |
| 2 | 3 | 0.002 | 1.998000000000009 | -0.002 |
| 2 | 3 | 0 | 2 | 0 |
| 0 | 3 | 0 | 0 | 0 |
| 1 | 5 | 0.001 | 0.9990000000000046 | -0.001 |

非零案例的最大密集质量守恒残差约为 4.6e-15 至 9.1e-15 kg。Canonical 1.0 示例和 legacy 0.1 未显式步长的零流量任务均通过 SUCCEEDED、validation PASS、sealed 和完整性核查；后者使用采样周期作为标准积分步长。10 kg 载体、0.1 m 恒体积贮箱、零偏置和单位安装矩阵均为试验夹具，不是装机或 CAD 证据。

## 热控下一批接入前已核实的差异

详细证据见 `docs/engineering/p9-n-thermal-native-feasibility.md`。现有热节点 builder 运行的是项目 Python SysModel；放进 Basilisk 调度器不改变物理模型来源。官方 `SensorThermal` 的初始温度和 TemperatureMsg 均是摄氏度，Kelvin 输入需显式转换；其辐射板模型没有 ambient/tau/heat_gain 参数。官方 2.11.0 的常数与其他太阳通量常量并不完全相同，且没有太阳距离平方反比缩放。已完成六组真实原生探针，温度响应和温标得到验证，但尚未交付原生热节点 TaskSpec。后续应使用独立物理参数和后端输出合同，避免填入虚构的等效时间常数或增益。
