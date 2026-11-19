# P9-O3：整星统一运行图事件证据闭环

日期：2026-11-19  
基线：P9-O2 squash merge `7207244a660db732ea321a0c9c64b28df0912c08`  
状态：O3-A guided event catalog gate 已进入 main（a96d7fed，该 main commit 尚无可见 Checks）；O3-B 的八类整星事件 profiles、正式 A/B Run Bundle 测试与 direct TaskSpec admission audit 已提交候选分支，等待官方 CI；O3-C/D 尚未完成。

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
