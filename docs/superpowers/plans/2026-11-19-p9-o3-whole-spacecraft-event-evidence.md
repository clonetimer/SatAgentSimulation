# P9-O3：整星统一运行图事件证据闭环

日期：2026-11-19  
基线：P9-O2 squash merge `7207244a660db732ea321a0c9c64b28df0912c08`  
状态：O3-A 已合入 main；O3-B 八类整星事件物理 A/B 与 sealed Run Bundle 已在官方 Basilisk 2.11.0 通过；O3-C 九条 Chromium→Basilisk 旅程已接入官方 workflow，当前 head fb42f71a0ed07b3bb76cbb05803787779181267e 的同头 CI 运行中；O3-D 归档与最终准入待 O3-C PASS 后收尾。

## 目标

在 P9-O2 已完成 EPS、Comm/Data 独立分系统事件准入后，收敛 `whole_spacecraft.unified_native.v1` 的整星事件暴露边界。整星已有 Basilisk 统一运行图和跨分系统耦合，但事件目录比正式 effect-evidence registry 更宽；不能将独立分系统或直接 adapter 测试等同于整星耦合证据。

## 当前审计基线（2026-11-19）

- Capability: `whole_spacecraft.unified_native.v1`，`trust_level=engineering_preview`、`status=agent_ready_candidate`。
- `modes.*.effects` 暴露 14 类事件：fault 7、degradation 5、constraint 2。
- 当前 effect-evidence registry 对该 Capability 有 6 个 `VERIFIED_NATIVE_EFFECT` profiles：
  - fault：`payload_instrument_off`、`comm_data_downlink_link_loss`、`eps_battery_capacity_loss`
  - degradation：`solar_panel_efficiency_loss`、`thermal_radiator_rejection_loss`
  - constraint：`power_safe_mode_threshold`
- 其余 8 个事件尚无该 Capability 自身的 `VERIFIED_NATIVE_EFFECT` profile：
  - `adcs_rw_jamming`、`adcs_rw_motor_failure`、`gyro_bias_step`
  - `gyro_noise_increase`、`rw_friction_degradation`、`adcs_reaction_wheel_speed_limit`
  - `propulsion_thruster_ignition_failure`、`propulsion_burn_impulse_loss`
- 现有测试证据并不等价：`test_unified_native_event_migration.py` 覆盖 6 个整星事件；`test_whole_propulsion_events.py` 覆盖 2 个推进事件；ADCS 相关测试主要验证 ADCS 独立分系统。尚不能据此声称 14 个整星事件均已完成 sealed Run Bundle A/B 与浏览器验收。
- `form_schema.py` 当前对 ADCS/EPS/Comm-Data 有 guided allowlist，但未对 whole-spacecraft 应用同类 allowlist；因此普通向导可能展示无整星 effect-evidence profile 的事件。

## O3-A：收紧普通向导事件目录

1. 在服务端 `GUIDED_EFFECT_ALLOWLISTS` 中，为 whole-spacecraft 增加精确 allowlist，只显示上述 6 个已有正式 profile 的事件。
2. 在前端 `addEffect` 增加同一 Capability + event-kind allowlist，避免 DOM/状态绕过。
3. 不删除底层运行时实现，不修改现有 advanced TaskSpec/数据集内部接口；本阶段只收敛普通向导目录。直接 TaskSpec 的准入边界需在 O3-B 独立审计，不能把 UI 隐藏误称为运行时拒绝。
4. 增加 form-schema 契约测试：fault 3、degradation 2、constraint 1；8 个未验证事件不得出现在 guided catalog。
5. 增加真实 Chromium 目录测试：六个已验证事件可见，8 个未验证事件不可见；普通 nominal whole-spacecraft Run 路径不回归。

## O3-B：补齐整星级物理 A/B 与证据 profiles

对剩余事件逐项执行正式 TaskSpec nominal/injected 配对：
- 官方 Basilisk 2.11.0；
- SUCCEEDED、validation PASS、sealed、integrity PASS；
- runtime injection 直接应用成功，unsupported/failed application 为 0；
- 整星统一运行图的直接 recorder 物理量与 nominal 参数参照；
- 故障结束恢复或明确记录保护/安全模式状态；退化满足时间语义；
- 通过后才新增 `VERIFIED_NATIVE_EFFECT` profile 并扩展 allowlist。

优先顺序：
1. 整星 ADCS：卡滞、电机失效、陀螺偏置/噪声、轮摩擦、轮速约束；必须验证姿态闭环和资源耦合，不复用 subsystem 证据冒充 whole evidence。
2. 整星推进：点火失败、冲量退化；核查推力、推进剂、功耗和轨道响应的直接 recorder。
3. 对现有 6 个 profiles 做断言强度复核，尤其确认电池容量、太阳阵列和散热退化不仅改变 ratio 字段，还改变对应物理输出。

## O3-C：整星 guided browser journeys

- Nominal whole-spacecraft finite Run。
- 每个新增 verified event 至少一个真实 Chromium 用户旅程。
- 验证对象选择、事件隔离、TaskSpec 校验、异步 Basilisk Run、effect evidence、直接遥测、曲线、FMEA、sealed Run Bundle 和 Run Center 重开。
- 不把单纯目录渲染或 fixture-backed API 交互计为真实运行验收。

## O3-D：归档与准入

- JUnit XML 精确记录各事件旅程；上传整星配对 A/B 与 Run Bundle 证据。
- 所有 O3 物理证据与浏览器旅程必须来自同一最终 PR head。
- 未通过事件保持隐藏；单个事件失败不得阻塞其他已验证事件。
- 整星 Capability 保持 `agent_ready_candidate`，直到 evidence、browser、运行边界和 claim limitations 全部闭合；不得仅因 6 个事件通过就升级整体状态。

## 明确不在 O3-A

- 不重写 UnifiedNativeRuntime，不新增第二套 TaskSpec/Run 系统。
- 不扩展 RF/BER/PER、地面站过站或轨道几何模型。
- 不宣称飞行硬件标定或 flight validation。
- 不把 O2 子系统 A/B 结果复用为整星 A/B 证据。


## O3-B 首轮实现（2026-11-19，CI 待验）

- 为缺少整星自身 profile 的 8 个事件新增 VERIFIED_NATIVE_EFFECT profiles：6 个 ADCS、2 个推进。
- 新增 propulsion.initial_propellant_kg 与 propulsion.propellant_used_kg 逐样本 recorder 字段，由官方 Basilisk FuelTank recorder 直接计算；用于区分点火失败与短脉冲退化，不再只依赖 summary。
- Capability operator.effects.evidence_fields 与 operator.observability.effect_evidence 对齐所有 14 个整星 profile；formal contract test 逐项比较字段集合。
- 扩展 test_formal_effect_evidence_contracts.py：整星 formal runtime 用例由 6 增至 14，并增加 direct TaskSpec boundary test，确认 runtime 内部已实现但 whole capability 未声明的 adcs_imu_signal_loss 不能通过高级 TaskSpec 规划。
- 新增 test_native_whole_spacecraft_event_bundles.py：2 个 nominal 基线（ADCS、推进）复用，8 个 injected case 分别要求官方 Basilisk 2.11.0、SUCCEEDED、validation PASS、sealed、integrity PASS、直接 recorder A/B、整星资源反馈闭环；推进事件另比较推进剂消耗与轨道响应。
- 当前分支：feat/p9-o3-b-whole-spacecraft-event-evidence-20261119。上述新增 profile 和测试尚未经过官方 CI；在 CI 通过前，不扩展 O3-A guided allowlist，也不宣称 O3-B 完成。


## O3-B formal contract failure and correction (2026-11-19)

- PR #190 first official native run `44435542565` failed in `test_unified_effect_contracts_are_declared_and_registered`, not in Basilisk physics or the eight paired Run Bundles. The assertion compared operator effect `evidence_fields` with profile fields as exact sets.
- The only mismatch across all 14 whole-spacecraft profiles was `solar_panel_efficiency_loss`: the operator effect contract and direct observability contract already exposed `eps.battery_soc` as contextual telemetry, while the profile omitted it. The runtime trace records `eps.battery_soc` directly from the official battery recorder.
- Corrected the formal evidence contract by adding `eps.battery_soc` to that profile's evidence_fields, keeping the existing operator and observability declarations aligned. No physical assertion or effect semantics were weakened; battery SOC remains contextual evidence, while the effect-specific assertions still target solar efficiency ratio and solar-array output power.
- A small set-based audit across all 14 profiles found this was the sole operator/profile evidence-field mismatch. The updated PR head must rerun formal tests and the full official Basilisk workflow before O3-B can be accepted.


## O3-A 正式收尾（2026-11-19）

- PR #188 已合并，merge commit `a96d7feddbb051cf0cf590a3f8c5e4679eed348a`。
- Same-head official native workflow `44424648554` success；formal effect runtime 17 passed、1 deselected；whole-spacecraft guided catalog 2 passed；Chromium JUnit 13 tests、0 failures、0 skipped。
- Artifact `14484456607`（39,263 bytes），包含整星目录 Chromium 证据及 O2 六条 EPS/Comm-Data 真实 Run 旅程。O3-A 只验收六个已验证整星事件的目录隔离，不代表新增八个事件已完成运行证据。

## O3-B 当前实现分支与验收（2026-11-19）

- 当前主候选 PR #190，branch `feat/p9-o3-b-whole-spacecraft-event-evidence-20261119`；较早 PR #189 覆盖相近范围，待 #190 最终通过后合并/关闭重复候选，避免两套重复交付。
- O3-B 当前 14 个整星 effect profile 与 operator evidence fields / observability evidence fields 的静态集合审计为 14/14 一致。
- 最新字段修复 head `232021432da171810f199098e20129c6dc527ebe` 的官方 native workflow `44437771461` 正在运行；不能将静态一致性审计等同于 8 个 paired sealed A/B 通过。


## O3-B 推进命令直接证据补齐（2026-11-19）

- 对 PR #190 的实现说明进行源码逐项核对，发现原先代码只有燃料、推力与燃烧状态 recorder，尚未记录 `THRArrayOnTimeCmdMsg.OnTimeRequest`。已补上官方命令消息 recorder，并输出 `propulsion.burn_command_on_time_s` 与 `propulsion.nominal_burn_command_on_time_s`。
- 两个推进 effect profiles 现在将命令请求字段纳入 evidence_fields 和 runtime_assertions；paired A/B 明确断言 nominal 8 s、点火失败 0 s、冲量退化 2 s（0.25 ratio），并继续核对推进剂、推力和轨道响应。
- operator effects、capability trace_fields、observability.effect_evidence 与 evidence registry 已同步；14 个整星 profile 的三方 evidence field 集合静态审计仍为 14/14 一致。
- 最新 PR #190 head `20093933c2f8fb915ec5e059951009be06e46235` 的官方 native workflow `44439100609` 正在执行；只有此 head 全部 applicable checks 完成后才判断 O3-B。


## O3-B 参数准入修复（2026-11-19）

- 首轮 native run `44439252234` 在 O3-B 八组 sealed A/B 的 nominal fixture planning 阶段失败，错误为 `PARAMETER_PATH_UNCONSUMED`；ADCS/propulsion 组件及 O2 回归已先通过。
- 根因是 `whole_spacecraft.unified_native.v1` Capability 未声明实际被 unified runtime 消费的 `rw_initial_speed_rad_s_0/1/2`，导致 canonical parameters.values 与 model.config 兼容镜像各产生 3 个未知路径。
- 已补齐 RW1-RW3 初始速度参数及 RW4 pyramid-only 参数声明，单位 rad/s、范围 [-500,500]；runtime 对三轮构型显式 RW4 speed 采用 fail-closed；增加参数消费审计和官方 native workflow JUnit 检查。
- 最新代码 head 需以 PR #190 当前分支查询为准；重新执行完整官方 Basilisk workflow 后，才可将 O3-B 八项 sealed A/B 标记为通过。


## O3-B ADCS 二值电机失效语义回归修正（2026-11-19）

- PR #190 首轮官方 native workflow 在旧 `test_p9n5_adcs_guided_event_catalog.py` 失败：实际向导给 `adcs_rw_motor_failure` 暴露了 `torque_scale`，而已冻结的产品合同要求它只有 `wheel_index`。这不是测试过时；历史 N5-E 决策明确要求 binary motor failure 与 partial torque-authority degradation 分开。
- 修复：`adcs_rw_motor_failure` guided schema 仅保留 wheel_index；whole 与 subsystem 两个正式 evidence profile 都断言 `effective_torque_ratio_{wheel_index} == 0`，不再引用 value_parameter；whole formal/paired tests 不再传 torque_scale。
- 参数准入同时收紧：`parameter_consumption` 不再接受 motor failure 的 torque_scale / remaining_torque_ratio；新增 planner-level 测试确认 advanced TaskSpec 传入 torque_scale=0.25 时以 PARAMETER_PATH_UNCONSUMED 拒绝。独立 `adcs_rw_torque_authority_loss` 仍允许 torque_scale=0.25 等部分退化。
- 运行时 `ProjectNativeRWCommandModel` 对 `adcs_rw_motor_failure` 固定施加 0 力矩；旧通用 alias `rw_motor_failure` 的兼容行为单独保留。这样即使直接绕过 guided form，也不会把 whole motor failure 变成部分力矩事件。
- 同步更新 fault catalog 与 whole-spacecraft 数据集生成器：motor failure 只采样目标轮号；部分力矩样本属于独立 degradation 条件。
- 原失败是旧契约回归，不是 Basilisk 物理失败。修复后的官方 Basilisk 2.11.0 native workflow 尚待最终 PR head 重跑，不提前记 PASS。

## O3-B 物理证据与 O3-C 浏览器门禁补齐（2026-11-19）

### O3-B 已验证的物理证据（旧 head b1df4c8）

- 官方 Basilisk 2.11.0 workflow `44449924799`：success；Artifact `14495582570`，74,276 bytes。
- 整星 paired Run Bundle 测试 `tests/test_native_whole_spacecraft_event_bundles.py`：9 passed（1 个参数消费/nominal 基线检查 + 8 个 injected 事件）。
- 八个事件均在整星统一运行图执行；每个 injected Run 要求 SUCCEEDED、validation PASS、sealed、integrity PASS、直接 runtime injection evidence 与事件专属 recorder A/B。
- Formal effect contract、整星事件目录、RW4 初始速度拒绝与现有 EPS/Comm-Data/ADCS 原生回归均在该 workflow 通过。
- 该结果只证明 O3-B 物理/TaskSpec/封存证据，不证明 O3-C 浏览器事件旅程已执行。

### O3-C 真实 Chromium 旅程接入（当前 head 84757e7）

- 发现 `test_guided_whole_spacecraft_event_run_bundle` 已写好 9 个真实浏览器到 Basilisk 旅程（nominal 1 + 8 events），覆盖向导选择、事件参数、TaskSpec 校验、真实 Run、effect evidence、直接 telemetry、plots、FMEA、Run Bundle 下载与逐文件 SHA-256、Run Center 历史重开；但原官方 workflow 的 pytest `-k` 没有选择该测试。
- 已为 9 个 journey 增加稳定 pytest IDs；官方 workflow 新增 collect-only 精确要求 9/9，真实执行时显式包含该测试，并在 JUnit XML 中要求九个 ID 全部存在、0 failure、0 skipped。
- 该浏览器门禁与 O3-B 物理门禁现在在同一 PR head `84757e7969254bddcaf3368ef7efd0b8fd531f4f` 上重跑；结果未出前不宣称 O3-C PASS。