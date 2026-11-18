# P9-N5 ADCS 分系统闭环：开发经验

日期：2026-11-19
状态：N5-A/B/C 已合入 main（PR #183）；N5-D 故障效果物理 A/B 仍在推进。

## 本轮完成的工程修正

1. **传感器坐标系必须在估计器边界统一。**
   - StarTracker 的 `qInrtl2Case` 是 N→C，使用实际 `dcm_CB` 组合为 N→B。
   - IMU 的 `AngVelPlatform` 属于 P 系，使用 `dcm_PB.T` 转回 B 系。
   - Magnetometer 的 `tam_S` 属于 S 系，使用 `dcm_SB.T` 转回 B 系；惯性系磁场参考来自官方 `MagneticFieldMsg.magField_N`。
   - 非零安装角测试应同时检查姿态和角速度，而不能只检查默认零安装角。

2. **磁强计不能只接线、不参与估计。**
   - 原实现虽然有官方 Magnetometer recorder，但融合模块读取后没有使用。
   - 现已把 Sun/Mag TRIAD 作为优先粗姿态来源；Sun 无效时尝试 Earth/Mag；向量退化时回退到有效星敏测量。
   - 通过 `adcs.fusion.magnetometer_used` 和磁场参考/本体系测量记录实际参与情况。

3. **Basilisk 2.11.0 IMU 启动样本不是有效零角速度。**
   - 对照官方 `AVSLab/basilisk@v2.11.0` 的 `src/simulation/sensors/imuSensor/imuSensor.cpp`：构造时 `NominalReady=false`；第一次 `UpdateState` 写出零值 payload，随后才将 `NominalReady=true`。第二次及后续调用才输出由当前 spacecraft state 计算的测量。
   - 不能用“角速度是否为零”推断样本有效，因为零角速度本身是合法物理状态。
   - `ImuFaultInjector` 现检查官方消息的 `isWritten()` 与 `timeWritten()` 递增，识别启动零样本；消息时间没有推进时保持无效。
   - 显式发布 `adcs.sensor.imu_sample_valid` 与 `adcs.fusion.body_rate_valid`。估计器在首个有效样本到达前保持上一有效角速度，不把启动零值伪装成测量。
   - 信号丢失、卡零和轴丢失事件会将完整三轴角速度样本标记为无效；偏置/噪声退化仍是有数据的测量。
   - Run Bundle、表单字典、场景模板、快速仿真结果分组与 Chromium 旅程均需包含该有效性字段，避免只在内部代码里修正。

4. **不要把 recorder 的 Reset 快照当成动态测量。**
   - 姿态真值/星敏初始姿态可在 Reset 快照中使用；IMU 需要完成官方模块首轮初始化后才有有效测量。
   - 物理测试应按 `body_rate_valid == 1` 选择样本，并同时检查官方原始平台系测量、故障注入后的平台系测量、本体系估计角速度和 spacecraft 真值。
   - 禁止固定跳过某个样本索引作为永久规则；有效性应由运行时消息状态显式给出。

5. **新增向导入口必须同步更新既有首页契约。**
   - 首页增加“分系统独立仿真”后，原测试仍断言欢迎选项数量为 8，实际应为 9。
   - 测试现在检查新增入口存在且名称正确，而不是只把数字机械改大。

## 当前物理与产品边界

- 仍是确定性工程级姿态估计器，不是 MEKF/Kalman，也没有飞行相关精度认证。
- 官方 StarTracker 没有星表、星点识别、成像/FOV 遮挡模型。
- Sun/Earth 向量是项目理想几何测量；官方 CSS 仍是标量照度输出，不能标称为三轴方向测量。
- 官方 IMU 是角速度/比力测量输入，不是完整捷联惯导解算。
- 官方磁场使用当前配置的 Basilisk 磁场环境；不等于已完成 WMM/IGRF 高阶模型的飞行精度验证。
- 当前只开放 nominal、finite/offline ADCS；故障/退化、实时运行、整星耦合与飞行软件级估计器仍未开放。

## 本轮 CI 证据与后续动作

- PR #183 首轮官方 Basilisk 2.11.0 门禁：传感器坐标系专项中，首个 Reset/IMU 初始化样本比较失败（估计角速度 0.0，真值 0.01）；根因与上游 IMU 首次 UpdateState 输出零 payload 的实现一致。
- 同一轮通用工作台浏览器门禁：欢迎选项数量由 8 增至 9 后，旧断言未同步更新。
- 本轮修正已加入 IMU 消息时间戳驱动的 readiness、有效角速度保持、trace 字段和测试覆盖；首页测试已验证分系统入口。
- **最终验收已完成：**PR #183 最终 head `cba89c24ed872c0f84c40430449660fe6fbdbb2e` 的官方 Basilisk 2.11.0、ADCS sealed Run Bundle、Chromium 真实向导和共享回归全部完成；20 个 workflow runs 为 19 success、1 条件性 skip、0 failure。


## PR #183 最终同 head 验收与合并（2026-11-19）

- PR #183 已 Squash merge，main commit `accb65503f5a535dad2b355120e6e59fe59bd83f`；受测 head `cba89c24ed872c0f84c40430449660fe6fbdbb2e`。
- 20 个 GitHub Actions workflow runs：19 success、1 条件性 skip、0 failure/cancelled。
- 官方 Basilisk 2.11.0 证据：ADCS 闭环与非零安装角坐标变换 2 passed；sealed Run Bundle identity/non-identity mount 2 passed。
- Chromium 5 passed、4 deselected；浏览器执行 ADCS 分系统向导、真实 Run、遥测/曲线、多速率遥测和完整 Run Bundle 下载。
- Artifact `native-component-physical-evidence`：ID `14454689028`，13,642 bytes；ZIP SHA-256 `4ccf8d0f7c53546be477930859bc55b536225f5bc654cbb7a25b3a704ee6fc32`。
- 经验补充：Basilisk 2.11.0 IMU 首次 UpdateState 会写入零初始化 payload，不能以“非零值”判断有效性；必须依赖消息写入时间戳推进。Reset recorder 快照不能替代有效动态测量。
- 当前仍未验证的范围：RW jamming/motor failure、gyro bias/noise、torque authority loss 的物理效果对照；实时 ADCS、整星惯量/装配和跨分系统耦合。


## N5-D：故障/退化物理 A/B 测试设计（2026-11-19）

新增 `tests/test_native_adcs_fault_effects.py`，对五类 effect 分别执行 nominal/injected 成对 Run，统一复用 `adcs_unified_native` 模板与 official Basilisk Process/Task。每次运行都经过 `prepare_run → execute_prepared_run → verify_run_bundle`，不是直接调用 Python 简化模型。

关键验收原则：

1. **卡滞不等于标签。** 要看实际送入官方 ReactionWheelStateEffector 的反向制动力矩、目标轮速下降和 effective torque limit。
2. **电机完全失效与部分力矩权限下降必须分开。** motor failure 的 torque scale=0；authority loss 使用 0.25 等非零比例，验证 effective max torque 和 applied torque 真实受限。
3. **陀螺偏置要比较 raw 与 measured。** 在有效 IMU 样本中检查 measured−raw 与 bias 参数一致，并确认 estimator 的 body-rate input 跟随。
4. **陀螺噪声要看噪声 recorder。** 不能只检查 event.active_effects；比较 nominal 的零噪声与 injected 的噪声幅度，并确认 IMU 样本仍有效。
5. **物理 A/B 与 Run Bundle 必须配对。** 每个 nominal 与 injected case 均 sealed + integrity PASS，避免只有一个临时结果或 API 直调。
6. **不要混淆 Operator verification 与 effect evidence classification。** `operator.effects[].verification` 仅允许 declared/candidate/not_observable；原生物理资格由 `effect_evidence_v32.json` 中的 `VERIFIED_NATIVE_EFFECT` profile 和 runtime assertions 表达。测试通过后补 profile/断言，不得写入 schema 不支持的 `verified`。

当前候选分支：`feat/p9-n5-d-adcs-fault-physical-ab-20261119`。最终结论以该分支 PR 同一 head 的 Basilisk 2.11.0 JUnit、Run Bundle integrity 与共享 CI 为准。


## N5-D 开发经验：故障/退化物理 A/B（2026-11-19）

1. **二值故障与连续退化必须使用不同 effect ID。**
   - `adcs_rw_motor_failure` 表示电机完全失效，目标轮实际驱动力矩必须为 0；不能把 `torque_scale=0.25` 塞进 motor failure 来表达部分失效。
   - `adcs_rw_torque_authority_loss` 专门表达剩余力矩权限比例，0.25 的有效力矩上限和实际送入 Basilisk 的力矩都必须受限。
   - A/B 测试分别核对原始控制命令、有效力矩比例、有效最大力矩和实际执行力矩。

2. **卡滞与电机失效不是同一种执行器行为。**
   - 卡滞仍允许模型施加有界反向制动力矩，同时将正常驱动权限记为 0。
   - 电机失效则实际输出力矩归零，不应产生卡滞制动力矩。
   - 只看 fault label 或 torque limit 会混淆两种物理含义，必须同时检查 applied torque 和 wheel speed。

3. **偏置和噪声退化要在有效样本上比较。**
   - IMU 首次 UpdateState 的初始化零样本不参与测量有效性断言。
   - 偏置用 raw platform rate 与 measured platform rate 的差值核对，并观察融合 body-rate 输入是否变化。
   - 噪声用噪声 recorder 核对幅度，同时确认 IMU sample valid 不因噪声增大而错误置为无效。

4. **Operator verification 与 effect evidence classification 是两套不同字段。**
   - `operator.effects[].verification` 的 schema 仅允许 `declared`、`candidate`、`not_observable`，不能写不存在的 `verified`。
   - 原生效果物理资格由 `effect_evidence_v32.json` 的 `classification=VERIFIED_NATIVE_EFFECT` 和逐项 `runtime_assertions` 表示。
   - 只把事件标成 `declared` 不等于有物理证据；profile 必须有 direct recorder assertion，并由同一 Basilisk head 的 A/B 测试支持。

5. **物理测试通过不等于自动开放 UI。**
   - N5-D 仅验证事件效果，PR 不修改前端，因此 Chromium 浏览器步骤按 scope 正确跳过。
   - ADCS 向导的 `#wizardFaultPanel` 仍保持隐藏，避免把后台已注册事件误当成用户已批准的产品能力。
   - 后续 N5-E 应只暴露通过物理门禁的五类事件，并增加真实 Chromium 事件配置、运行、结果和 Run Bundle 回放旅程。

6. **正式合同测试必须区分 binary motor failure 与 partial torque loss。**
   - 旧参数化测试曾把 `adcs_rw_motor_failure` 与 `torque_scale=0.25` 组合，语义上把二值故障和部分退化混为一谈。
   - 已修正为 motor failure 无比例参数，另增加 torque authority loss 的 0.25 formal case。
   - 工作流将 effect evidence registry contract 纳入 Basilisk 专项，防止有物理 A/B 测试却漏掉 evidence profile。

## N5-D 最终同 head 物理证据（2026-11-19）

- 受测 PR head：`973293f6e41b10ea3550ccb7766b6465c5755b1c`。
- Official Basilisk 2.11.0 专项：五组 nominal/injected A/B 全部通过，`5 passed in 24.44s`。
- 证据规则回归：`test_unified_effect_contracts_are_declared_and_registered`，`1 passed, 13 deselected`；正式合同已区分 binary `adcs_rw_motor_failure` 与 partial `adcs_rw_torque_authority_loss`。
- `adcs_rw_torque_authority_loss` 的独立 profile 使用 `effective_torque_ratio_{wheel_index} == torque_scale` 作为运行时断言，并保留 effective max torque 与 applied torque 作为证据字段。
- Artifact `native-component-physical-evidence`：ID `14460007797`，14,211 bytes。
- Chromium 步骤因 PR 不包含前端或浏览器测试文件而按 path-scope 规则跳过；这是预期行为。ADCS 故障面板仍隐藏，后续 N5-E 单独开放已验证事件并执行浏览器验收。

## N5-E 开发经验：受控开放 ADCS 事件向导（2026-11-19）

1. **物理证据 allowlist 必须落在服务端 form schema。** 仅在前端隐藏选项不足以约束 API 返回的可选事件；`subsystem.adcs_unified_native.v1` 的 `event_catalog` 只返回 N5-D 通过 A/B 的五个 effect。
2. **事件字段名必须与 runtime 注入器读取键完全一致。** `gyro_bias_step` 读取 `bias_step_deg_s`，`gyro_noise_increase` 读取 `noise_scale`；旧 schema 中的 `bias_deg_s` / `noise_multiplier` 不会被 runtime 消费。
3. **二值失效与连续退化保持不同 effect ID 和 UI 参数。** motor failure 只有 wheel_index；torque authority loss 才允许 torque_scale。
4. **事件类别由非空 catalog 决定。** ADCS 只显示 fault/degradation，constraint catalog 为空时隐藏运行约束选项。
5. **向导入口范围要精确。** 只对 `goal=subsystem_native` 且 capability 为 unified-native ADCS 显示故障面板；单机 native 与其他未验证分系统继续隐藏。
6. **UI 验收必须真实提交 TaskSpec。** 浏览器测试不止检查选项文本，还要配置起止时间与参数、执行官方 Basilisk、验证 injection evidence PASS、有效力矩比例和 sealed Run Bundle ZIP 哈希。

## CI 工程经验：PR 浅克隆的浏览器范围检测（2026-11-19）

- `actions/checkout` 在 pull_request 上检出 `refs/remotes/pull/<n>/merge`，默认浅克隆只含 merge HEAD；再以 depth=1 fetch PR base SHA 后，base 与 HEAD 之间可能没有可计算的 merge-base。
- 三点式 `git diff BASE...HEAD` 因此会报 `no merge base`。如果该命令位于 `if` 条件中，shell 可能把它当成条件不匹配而不使步骤失败，导致浏览器测试被静默跳过。
- 对 PR merge checkout 的路径门禁应使用 `git diff --name-only BASE HEAD` 两点式树比较，并打印路径清单；它直接比较 base tree 与 merge tree，不依赖完整提交祖先。
- 对浏览器门禁，必须检查步骤是否实际执行，而不能把 skipped 误读为 PASS。
- **pytest `-k` 必须与真实测试函数名核对。** N5-E 浏览器函数名为 `guided_native_adcs_wizard_runs_verified_torque_authority_degradation`，旧过滤词 `guided_native_adcs_fault` 没有匹配该函数；因此工作流虽显示 5 passed，实际未执行故障向导测试。修正为精确函数名，并以 pytest 的 `passed/deselected` 数量及日志中的测试名确认覆盖，不能只看 job conclusion。
- N5-E 的负向 UI 合同同时检查 `#wizardFaultPanel`、`#wizardEventHost` 和 `#wizardNativeAdcsFaultNote` 均隐藏；只隐藏事件列表而保留空的“故障与事件”面板仍属于入口泄漏。