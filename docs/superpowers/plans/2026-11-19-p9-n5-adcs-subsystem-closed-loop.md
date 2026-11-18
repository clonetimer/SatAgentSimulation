# P9-N5：ADCS 分系统独立闭环仿真产品化

日期：2026-11-19
状态：N5-A/B/C 已完成并合入 main（PR #183）；N5-D 故障效果物理 A/B 验证继续推进。

## 目标

在统一 Basilisk Process/Task 中完成 ADCS 分系统闭环：航天器刚体与反作用轮动力学、官方 StarTracker / IMU / Magnetometer 测量、项目明确标注的辅助向量传感器、姿态估计、惯性参考、MRP feedback 控制、轮电机力矩和真实 ReactionWheelStateEffector。通过正式 TaskSpec → registered adapter → sealed Run Bundle → Chromium 用户旅程交付。

不把 component 单机 Run 拼接成分系统闭环；不另建 ADCS runtime；不将该工程预览描述成飞行软件或飞行相关估计器。

## 当前代码审计发现

1. 当前 `AdcsSensorFusion` 读取 Magnetometer `tam_S` 后直接丢弃；磁强计并未参与估计。
2. `StarTracker.qInrtl2Case` 是惯性到 case 的姿态，`dcm_CB` 是 body 到 case；当前融合直接将 case MRP 当作 body MRP，非零安装角时坐标系不一致。
3. `ImuSensor.AngVelPlatform` 属于 platform frame P；当前融合直接写入 `NavAttMsg.omega_BN_B`，非零 IMU 安装角时坐标系不一致。
4. `Magnetometer.tam_S` 属于 sensor frame S；`dcm_SB` 是 body 到 sensor，融合若使用该数据必须先转换回 body frame。
5. `MagneticFieldBase.envOutMsgs[0].magField_N` 是惯性系 N 的磁场参考，可与 Magnetometer body-frame 测量组成 TRIAD 向量对。
6. `EarthHorizonSensor` 文档声称不进入控制回路，但实际被 `AdcsSensorFusion` 用作 coarse TRIAD 输入；它是项目理想几何传感器，不是官方 Basilisk Earth sensor。
7. `SunDirectionSensor` 同样是项目理想几何向量测量；官方 `CoarseSunSensor` 当前输出标量照度，不能称为三轴方向测量。
8. 当前融合采用 MRP 分量线性加权，计划改为四元数球面插值（SLERP），并明确它仍是确定性工程估计器，不是 MEKF/Kalman 滤波器。

## 物理与坐标系合同

- 惯性系：N；航天器本体系：B；星敏 case：C；IMU platform：P；磁强计 sensor：S。
- StarTracker 输出 `qInrtl2Case` 表示 N→C；`dcm_CB` 表示 B→C。融合前使用 `dcm_BC = dcm_CB.T` 将 N→C 转换为 N→B。
- IMU 输出角速度在 P 系；`dcm_PB` 表示 B→P。融合前使用 `omega_BN_B = dcm_BP * omega_BN_P`，其中 `dcm_BP = dcm_PB.T`。
- Magnetometer 输出 `tam_S` 在 S 系；`dcm_SB` 表示 B→S。融合前使用 `tam_B = dcm_SB.T * tam_S`。
- 磁场参考 `magField_N` 在 N 系；与 body-frame 磁场测量一起进入 TRIAD。退化/近共线时必须显式标记不可用并回退，不允许 NaN 或伪造有效姿态。
- `NavAttMsg.sigma_BN` 与 `omega_BN_B` 必须分别表达 B/N 姿态与 B 系角速度。
- 轮安装默认 orthogonal_3、零偏置安装位置；FSW 使用实际 Hub inertia / COM；轮电机命令经项目 fault manager 后进入官方 ReactionWheelStateEffector。

## 测量来源与适用范围

- 官方 Basilisk：StarTracker、ImuSensor、Magnetometer、MagneticFieldCenteredDipole、ReactionWheelStateEffector、inertial3D、attTrackingError、mrpFeedback、rwMotorTorque、Spacecraft。
- 项目 SysModel：IMU/StarTracker fault injector、RW command fault manager、SunDirectionSensor、EarthHorizonSensor、AdcsSensorFusion。
- Sun/Earth 向量是由真实环境与航天器状态生成的理想项目测量；不是官方 CSS 三轴输出，也不包含光学/FOV/遮挡误差模型。
- StarTracker 使用官方模块对 SCStates 的姿态测量，不包含星表、光学成像、星点检测和 FOV 可见性。
- Magnetometer 使用官方磁场模型和官方传感器，经过安装变换后参与 coarse TRIAD；不代表 WMM/IGRF 高阶地磁精度已完成飞行验证。
- IMU 使用官方传感器模型，噪声/偏置由项目模块注入；当前不是完整惯导/MEKF。
- 该闭环为工程级 ADCS 单分系统试验台，不包含整星 CAD 惯量、柔性附件、推进/电源/热控耦合。

## 实施阶段

### N5-A：传感器坐标系与融合链修正 <已完成，PR #183>

1. 修正 StarTracker case→body 姿态转换。
2. 修正 IMU platform→body 角速度转换。
3. 修正 Magnetometer sensor→body 磁场转换，并接入惯性系磁场参考。
4. 使用 Sun/Mag TRIAD；Sun 不可用时使用 Earth/Mag；向量退化时 fail closed 或回退到有效 StarTracker。
5. 使用 quaternion SLERP 代替 MRP 分量线性平均。
6. 增加估计姿态、估计 body-rate、融合来源和磁强计使用标记遥测。
7. 修正文档中 EarthHorizonSensor 的实际用途说明。

### N5-B：正式 TaskSpec 与 sealed Run Bundle <已完成，PR #183>

1. 使用注册模板 `adcs_unified_native` 创建 canonical subsystem TaskSpec。
2. 官方 Basilisk 2.11.0 下执行 30–60 s nominal 闭环；验证初始/最终 pointing error、控制力矩、轮速和估计器输出。
3. 验证 non-identity StarTracker / IMU / Magnetometer mounting 下，融合输出仍处于 B 系且与真值一致。
4. 验证 Run 状态 SUCCEEDED、validation PASS、sealed、integrity PASS；导出完整 Run Bundle 并逐文件核验 SHA-256。
5. 仅在 nominal 闭环和安装变换验收通过后，将 capability `operator.effects.nominal` 标记为 verified。

### N5-C：ADCS 用户向导入口 <已完成，PR #183>

1. 在快速仿真增加“ADCS 分系统独立仿真”目标；只显示 `subsystem.adcs`。
2. 从服务端 Scenario Template 加载 `adcs_unified_native`，不在前端复制物理默认值。
3. 当前只开放 finite/offline nominal；未通过 N5-D 验收的 fault/degradation/constraint 不进入该向导。
4. 显示来源边界、坐标系、安装角、控制周期、轮配置、单位和适用范围。
5. 结果展示 pointing error、body rate、estimated attitude、RW speed/command/applied torque、sensor provenance。

### N5-D：真实 Chromium 旅程与故障效果

1. [x] 浏览器真实选择模板、校验、运行、读取曲线和完整 Run Bundle 下载；本轮 Chromium 旅程已验证。历史回放在同一 Run Center 记录中核验。
2. [x] 五类事件已完成官方 Basilisk 2.11.0 nominal/injected 配对物理 A/B，并且每一侧均 SUCCEEDED、validation PASS、sealed、integrity PASS。
3. [x] 五类事件均有直接 recorder 断言；torque authority loss 已补齐独立 EffectEvidenceProfile，不再与二值 motor failure 混用。
4. [x] ADCS 向导故障面板仍保持隐藏；本 PR 不扩展用户可选故障菜单。
5. [ ] 后续 N5-E 单独实施受控故障向导，只暴露本阶段已通过的五类事件，并以 Chromium 真实旅程验收。

## 验收门槛

- 同一最终 PR head：官方 Basilisk 2.11.0、坐标系/物理专项、TaskSpec sealed Run、预检、Chromium 真实用户旅程和共享回归均通过。
- 真实测量数据进入控制回路；禁止以 spacecraft truth 直接替代 attitude estimate。
- 安装变换非零时，估计姿态和 body-rate 必须正确转换到 B 系。
- 磁强计必须实际参与融合，或在产品/UI/文档中明确标记为仅 telemetry；本阶段目标为实际参与融合。
- 不能以曲线存在、模块实例存在或单测通过替代闭环物理响应。
- 未验证的 fault/degradation 不开放；单机实时和跨分系统耦合不在本阶段范围。

## N5-A/B/C 最终验收（2026-11-19）

- PR #183 已 Squash merge；merge commit：`accb65503f5a535dad2b355120e6e59fe59bd83f`。
- 最终受测 PR head：`cba89c24ed872c0f84c40430449660fe6fbdbb2e`。
- 20 个 PR workflow runs：19 success、1 条件性 skip、0 failure/cancelled。
- 官方 Basilisk 2.11.0：ADCS 闭环/坐标变换 2 passed；ADCS sealed Run Bundle（identity 与 non-identity mounts）2 passed。
- Chromium：5 passed、4 deselected（由工作流筛选条件主动排除）；真实 ADCS 用户旅程覆盖模板选择、预检、异步运行、遥测、曲线、多速率流、完整 Run Bundle 下载。
- Artifact `native-component-physical-evidence`：ID `14454689028`，13,642 bytes，SHA-256 `4ccf8d0f7c53546be477930859bc55b536225f5bc654cbb7a25b3a704ee6fc32`。
- 本阶段只准入 nominal、finite、offline ADCS；故障/退化、实时、整星惯量与跨分系统耦合仍未开放。

## 下一步：N5-D 故障效果物理 A/B

按顺序核查 RW jamming/motor failure、gyro bias/noise、torque authority loss。每项必须有同一物理初始条件的 nominal/control 与 injected case、实际生效 recorder、pointing error/body-rate/RW response 对照，以及 sealed Run Bundle。只有通过物理门禁的事件才可进入 ADCS 向导；其余保持隐藏/拒绝。


## N5-D 实施中的物理 A/B 矩阵（2026-11-19）

新增 `tests/test_native_adcs_fault_effects.py`，每个 case 都使用相同 ADCS 模板、相同初始条件和同一官方 Basilisk 2.11.0 runtime，分别执行 nominal baseline 与单一 injected event；两侧都必须生成 SUCCEEDED、validation PASS、sealed、integrity PASS 的 Run Bundle。

| 效应 | 直接物理观测 | A/B 通过条件 |
|---|---|---|
| `adcs_rw_jamming` | 有效力矩权限降为 0、制动力矩与轮速下降 | injected 轮速/执行器响应区别于 baseline；不是只看 fault label |
| `adcs_rw_motor_failure` | 目标轮有效力矩上限与实际执行力矩归零 | 原始控制命令仍存在，实际送入 ReactionWheelStateEffector 的目标轮力矩为 0 |
| `adcs_rw_torque_authority_loss` | 有效最大力矩按比例下降 | `torque_scale=0.25` 时有效力矩比例为 0.25，且实际力矩受限；与二值电机失效区分 |
| `gyro_bias_step` | IMU 平台系 bias、measured rate、融合 body-rate | measured−raw 与偏置增量一致，IMU 样本保持有效 |
| `gyro_noise_increase` | IMU 噪声 recorder 与有效性标记 | 噪声幅度可观察增加，不能把正常样本标记为无效 |

本阶段状态：五类 effect 的官方 Basilisk 2.11.0 A/B 与 sealed Run Bundle 已通过。证据状态分两层：

- `operator.effects[].verification` 是 capability 合同字段，schema 只允许 `declared`、`candidate`、`not_observable`；不得写入不存在的 `verified`。
- `effect_evidence_v32.json` 使用 `classification=VERIFIED_NATIVE_EFFECT` 和逐 effect `runtime_assertions` 表示已有原生物理证据。五类事件均由独立 assertion 绑定直接 recorder 字段。
- `adcs_rw_motor_failure` 是二值失效，不接受部分 torque scale；`adcs_rw_torque_authority_loss` 单独承载 `torque_scale` 的连续权限下降。
- 本 PR 不改变向导故障菜单；`#wizardFaultPanel` 继续隐藏。仅在后续 N5-E 中增加经过验收的五类事件，并单独通过 Chromium。


## N5-D 同 head 证据补强（2026-11-19）

- 五个封存 A/B 测试已在 PR #184 的官方 Basilisk 2.11.0 工作流中全部通过。
- 新增 `adcs_rw_torque_authority_loss` 的 EffectEvidenceProfile，runtime assertion 将 `effective_torque_ratio_{wheel_index}` 与事件 `torque_scale` 对照；同时保留实际执行力矩 recorder 作为物理证据。
- 正式 effect contract 回归现在把 `adcs_rw_motor_failure` 作为二值失效（不传 `torque_scale`），并独立加入 `adcs_rw_torque_authority_loss` 的 0.25 部分权限案例。
- 工作流新增 effect evidence registry contract 检查，防止物理 A/B 通过但 registry/profile 漏项。
- `operator.effects[].verification` 仍使用 schema 支持的 `declared`；`VERIFIED_NATIVE_EFFECT` 属于独立 evidence profile classification，不混为一个字段。
- 当前 PR 不改 UI，浏览器步骤按工作流 scope 判断主动跳过；ADCS 故障面板继续隐藏。N5-E 再单独开放五类已验证事件。

## N5-D 最终物理 A/B 门禁（2026-11-19）

- 受测 head：`973293f6e41b10ea3550ccb7766b6465c5755b1c`；专用工作流 #44374968938 已 PASS。
- 官方 Basilisk 2.11.0 故障/退化 A/B：5 passed（24.44 s），覆盖 RW jamming、binary motor failure、25% torque authority loss、gyro bias step、gyro noise increase。
- effect evidence registry contract：1 passed、13 deselected；确认所有登记 effect 的 profile 分类与 capability 合同存在。`adcs_rw_torque_authority_loss` 已补齐独立 `VERIFIED_NATIVE_EFFECT` profile。
- 物理证据 artifact：`native-component-physical-evidence`，ID `14460007797`，14,211 bytes。
- 本 PR 没有修改 web surface；Chromium 安装/浏览器步骤由 path-scope 条件主动跳过。ADCS `#wizardFaultPanel` 继续隐藏，浏览器故障选择器不在 N5-D 开放范围。
- 共享回归当时仍有 Agent input/run consistency 运行中；最终是否合并以最新 head 的全部 workflow 结束状态为准。

## N5-E 受控开放 ADCS 故障向导（实施中，2026-11-19）

- 从 N5-D 已通过的五类 effect 中建立服务端 allowlist；ADCS 向导只返回 3 fault + 2 degradation，constraints 为空。
- 其他 native 单机/分系统保持事件面板隐藏；未通过物理门禁的 ADCS 摩擦退化和速度约束不进入向导。
- 事件参数名与运行时注入器一致，并通过真实 Chromium UI 配置、TaskSpec、Basilisk Run、injection evidence 与完整 Run Bundle 下载验证。
- N5-E 的最终验收以同一 PR head 的官方 Basilisk 2.11.0 与 Chromium CI 为准。