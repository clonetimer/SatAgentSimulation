# P9-O3 整星事件证据闭环：开发经验

日期：2026-11-19  
基线：P9-O2 squash merge `7207244a660db732ea321a0c9c64b28df0912c08`

## 初始审计发现

- Whole-spacecraft unified capability 的 `modes.*.effects` 声明 14 个事件，而 `effect_evidence_v32.json` 只有 6 个该 Capability 自身的 `VERIFIED_NATIVE_EFFECT` profiles。
- `form_schema.py` 的 guided allowlist 已覆盖 ADCS、EPS、Comm/Data，但遗漏 `whole_spacecraft.unified_native.v1`。因此普通向导会从 operator contract 派生出更宽的整星事件菜单。
- 现有直接运行测试覆盖范围分散：6 个整星基础事件在 `test_unified_native_event_migration.py`；2 个推进事件在 `test_whole_propulsion_events.py`；ADCS 事件主要在 subsystem ADCS 测试。它们不能自动合并为 14 个整星级正式 effect profiles。
- 正确修复方向不是删除底层运行时或伪造 profiles，而是先按正式证据收紧 guided catalog，再对剩余事件逐项做整星 nominal/injected sealed Run Bundle A/B。

## O3-A 实施原则

- 服务端 form schema 是普通 guided event catalog 的权威来源；前端 add-event 再做同一 allowlist 防御。
- O3-A 只限制普通向导展示范围，不谎称直接 TaskSpec/advanced path 已拒绝未验证事件；该执行边界单独审计。
- 当前可开放的整星事件为 fault 3、degradation 2、constraint 1；未验证的 8 个事件保持隐藏，直到整星级证据通过。


## O3-B 实施经验（2026-11-19，CI 待验）

1. 整星证据必须使用 whole_spacecraft.unified_native.v1 自己的 recorder 和 Run Bundle。ADCS subsystem 的 7 个 profile 不能直接复制成整星 profile，必须通过 whole graph 的 nominal/injected 对照。
2. 反作用轮参数证据必须使用 {wheel_index} 模板，而不是固定 wheel 0；formal helper 将模板替换为 wildcard QoI，runtime assertion 按事件参数绑定实际轮号。
3. 推进点火与冲量退化是一次性定时命令，不是持续的 actuator degradation。点火失败验证零推力/零推进剂消耗；冲量退化验证缩短的官方 thruster on-time、推进剂消耗减少及轨道响应变化。
4. 新增逐样本 propulsion.propellant_used_kg，由 FuelTank recorder 当前质量减去首个 recorder 样本计算；summary 仍保留累计 propellant_used_kg。逐样本字段用于 profile，summary 用于 paired A/B。
5. propulsion_burn_on_time_s 在 unified runtime config 中是标量，内部再包装为 on-time 序列；不要因为 PropulsionBurnController 构造函数接受 Sequence 就把 TaskSpec 参数写成数组。
6. 轮速约束的保护动作会在首次越限样本立即施加，数秒后轮速可能已降至阈值以下。验证制动时应在整个 active window 查找“越限且有制动命令”的样本，不要只检查窗口中点。
7. O3-A 只限制 guided catalog 不等于 direct TaskSpec 已拒绝未声明事件。新增 planning-level 测试用一个 runtime-known、capability-undeclared 的 IMU event 检查高级 TaskSpec 边界；必须以 planner 结果为准。
8. 当前所有新增断言均待官方 Basilisk 2.11.0 CI 验证；不得把 profile JSON 注册、direct adapter PASS 或 test collection 当作 sealed A/B PASS。


## O3-B：whole-spacecraft evidence-field parity 回归（2026-11-19）

- 14 个 whole-spacecraft VERIFIED_NATIVE_EFFECT profile 与 operator effect contract 做精确字段集比较时，唯一差异是 `solar_panel_efficiency_loss`：operator 与 capability observability 声明了 `eps.battery_soc`，profile 未声明。
- `eps.battery_soc` 是整星运行图中直接从官方 SimpleBattery recorder 计算并输出的逐采样字段，可作为太阳阵效率退化的系统上下文证据；它不是本事件的独立效果判据。核心断言仍是太阳阵效率比和太阳阵输出功率变化。
- 已把 `eps.battery_soc` 补入该 profile 的 evidence_fields，保持 operator、observability 与 profile 三方精确一致；不通过放宽集合断言或删减已有 operator 字段来让测试通过。
- 首轮失败发生在 formal contract parity test；该轮 native component、EPS/Comm-Data 物理 A/B 等先前步骤通过。字段修复后的完整 CI 结果尚待验证。


## O3-A 收尾经验（2026-11-19）

- 整星普通向导目录收敛为 fault 3、degradation 2、constraint 1；底层 14 个运行时声明保留，未验证的八项只在普通向导隐藏。不能把 UI 隐藏说成高级 TaskSpec 已拒绝。
- PR #188 同一 head 的正式 native workflow、17 项 formal runtime、2 项 catalog contract 和 13 项真实 Chromium JUnit 均通过；Artifact `14484456607` 保留完整证据。
- O3-A 的 Chromium 旅程只验证目录隔离；O3-B/C 仍必须逐事件做 whole-spacecraft sealed A/B 与真实运行旅程。


## O3-B 推进事件直接命令证据补齐（2026-11-19）

- PR 说明曾声明记录官方 `THRArrayOnTimeCmdMsgPayload.OnTimeRequest`，但代码审计发现 trace 实际只有燃烧状态、推力和燃料记录，没有逐采样命令请求字段。仅靠燃料消耗与轨道响应能证明结果变化，但无法直接审计控制器施加的命令持续时间。
- 已将 `PropulsionBurnController.commandOutMsg` 纳入统一 recorder map，并新增 `propulsion.burn_command_on_time_s`（各推进器请求 on-time 的最大值）和 `propulsion.nominal_burn_command_on_time_s`（TaskSpec 配置标称值）。这两个字段来自实际官方 THRArrayOnTimeCmdMsg recorder，不是根据事件参数反推的派生值。
- 点火失败 profile 现在要求事件期间实际 OnTimeRequest 为 0；冲量损失 profile 要求实际请求小于标称请求且大于 0。paired Run Bundle 同时断言 nominal 8 s、ignition failure 0 s、impulse loss 2 s（配置比例 0.25），并保留燃料消耗、推力及轨道响应 A/B。
- 新增字段已同步 capability trace_fields、operator effect evidence、observability effect_evidence 与两个 formal profiles；字段集合需保持四方一致。官方 Basilisk CI 结果待当前 PR head 验证。


## O3-B 推进命令请求 recorder 补齐（2026-11-19）

- 代码审查发现 PR 描述声称记录官方 THRArrayOnTimeCmdMsg.OnTimeRequest，但实际 trace 只有 burn status、推力和燃料记录。仅用燃料/轨道结果无法直接审计控制器请求的燃烧持续时间。
- 已把 `PropulsionBurnController.commandOutMsg` 作为独立消息加入 recorder map，并按每个采样记录最大单推进器请求 on-time `propulsion.burn_command_on_time_s`；`propulsion.nominal_burn_command_on_time_s` 取 TaskSpec 标称配置值，不从事件参数反推。
- ignition failure formal profile 要求命令请求为 0；impulse loss profile 要求命令请求小于标称值且大于 0。paired A/B 进一步断言 8 s → 0 s 与 8 s → 2 s，并保留 fuel/thrust/orbit A/B。
- 这使“控制命令变化”和“官方 Thruster/FuelTank 物理结果”形成两层独立证据。新增字段已同步 capability trace_fields、operator effects、observability 和 effect registry。最终结论等待官方 Basilisk 2.11.0 CI。


## O3-B TaskSpec 参数字典缺口与修复（2026-11-19）

- 八个整星事件 paired bundle 首轮未进入 Basilisk：`prepare_run` 的 planning 阶段对共享 nominal ADCS baseline 返回 6 个 `PARAMETER_PATH_UNCONSUMED`。根因是测试使用了真实运行时读取的 `rw_initial_speed_rad_s_0/1/2`，但 `whole_spacecraft.unified_native.v1` Capability parameters 没有声明这些键；每个键同时出现在 canonical `parameters.values` 和兼容镜像 `model.config`，所以审计报告 3×2=6 个未知路径。
- 这些参数确实被 `unified_native.py` 消费：分别设置官方 Basilisk HR16 RW 的初始 Omega，单位 rad/s，并在 `rwFactory.create(Omega=...)` 前转换为 RPM。它们属于正式用户参数，不是测试专用参数，因此正确修复是补 Capability schema，而不是删除测试值或绕过 parameter audit。
- 已将 RW1-RW3 初始速度及 RW4 初始速度（仅 pyramid_4）加入 Capability parameters，约束 -500～500 rad/s；新增运行时检查，三轮构型显式设置 RW4 初始速度时 fail closed，避免被静默忽略。
- paired test 文件增加 ADCS/propulsion baseline 参数消费审计；官方 native workflow 增加 RW4 ghost-parameter rejection JUnit 检查。下一次 CI 应先验证 planning PASS，再进入八个 Basilisk sealed A/B。


## O3-B ADCS 二值电机失效语义回归修正（2026-11-19）

- PR #190 首轮官方 native workflow 在旧 `test_p9n5_adcs_guided_event_catalog.py` 失败：实际向导给 `adcs_rw_motor_failure` 暴露了 `torque_scale`，而已冻结的产品合同要求它只有 `wheel_index`。这不是测试过时；历史 N5-E 决策明确要求 binary motor failure 与 partial torque-authority degradation 分开。
- 修复：`adcs_rw_motor_failure` guided schema 仅保留 wheel_index；whole 与 subsystem 两个正式 evidence profile 都断言 `effective_torque_ratio_{wheel_index} == 0`，不再引用 value_parameter；whole formal/paired tests 不再传 torque_scale。
- 参数准入同时收紧：`parameter_consumption` 不再接受 motor failure 的 torque_scale / remaining_torque_ratio；新增 planner-level 测试确认 advanced TaskSpec 传入 torque_scale=0.25 时以 PARAMETER_PATH_UNCONSUMED 拒绝。独立 `adcs_rw_torque_authority_loss` 仍允许 torque_scale=0.25 等部分退化。
- 运行时 `ProjectNativeRWCommandModel` 对 `adcs_rw_motor_failure` 固定施加 0 力矩；旧通用 alias `rw_motor_failure` 的兼容行为单独保留。这样即使直接绕过 guided form，也不会把 whole motor failure 变成部分力矩事件。
- 同步更新 fault catalog 与 whole-spacecraft 数据集生成器：motor failure 只采样目标轮号；部分力矩样本属于独立 degradation 条件。
- 原失败是旧契约回归，不是 Basilisk 物理失败。修复后的官方 Basilisk 2.11.0 native workflow 尚待最终 PR head 重跑，不提前记 PASS。

## O3-B / O3-C 交付门禁经验（2026-11-19）

1. **测试存在不等于测试执行。** PR 中已有九条整星 Chromium→Basilisk 真实 Run 测试，但官方 workflow 的 `-k` 只选了目录测试；所以虽然测试代码完整、目录浏览器通过，八类事件的用户旅程仍未被验收。今后每个重要用户旅程必须有 collect-only 精确数量门禁，并在 JUnit 中逐条核对 testcase、failure 和 skipped。
2. **物理与浏览器证据分开计数。** O3-B 的 9 passed 是整星物理 A/B（nominal/参数消费检查 + 八个 injected sealed Run）；不能拿它替代 O3-C 的 9 条 Chromium 用户旅程。
3. **稳定的 pytest IDs 有助于证据追踪。** 给 whole-spacecraft nominal 和八类事件加固定 IDs，workflow 以 ID 集合核对，避免仅检查函数名而遗漏某个参数化场景。
4. **O3-C 的用户旅程必须验证端到端闭环。** 每条真实 Chromium 路径需要经过向导、TaskSpec、官方 Basilisk Run、effect evidence、直接 telemetry、plots、FMEA、sealed Bundle 的 manifest/逐文件 SHA-256 与 Run Center 历史重开；单纯渲染事件目录不够。
5. 当前 O3-C 门禁已接入官方 native workflow，但 head `84757e7969254bddcaf3368ef7efd0b8fd531f4f` 的完整 CI 尚未完成。不得把 collect-only 9/9 当成真实运行 9/9 PASS。
## O3-C Chromium 参数契约回归（2026-11-19）

- 将整星 motor failure 加入真实浏览器旅程后，发现参数化用例仍提交 `torque_scale=0.0`，而本阶段已冻结的 `adcs_rw_motor_failure` 只有 `wheel_index`；部分力矩损失必须使用独立 degradation `adcs_rw_torque_authority_loss`。
- 这是 browser fixture 未随服务端 schema 一起迁移，不是 Basilisk 物理问题。已将 Chromium 参数改为 `{"wheel_index": 0}`，使表单编辑、canonical TaskSpec 与 runtime binary actuator 行为一致。
- 经验：对每个事件至少做三方参数契约检查（form parameter_fields、browser fixture、canonical TaskSpec），不能只依赖 native paired test 已通过。
- 修复提交 `dbd98a03a18807d5252d646f03a0f170076d7735`；上一 head `908d347f...` 的 CI 不包含此修复，最终结论必须等待新 head 的完整回归。
## O3-D 原始封存运行证据归档经验（2026-11-19）

- JUnit 与 direct-recorder JSON 是测试摘要，不等于原始 Run Bundle。测试若把 sealed bundle 放在 pytest tmp_path，CI 结束后会丢失原始任务、telemetry、manifest、seal 和逐文件哈希，无法独立复核。
- 对物理 A/B：在原 bundle integrity PASS 后复制整个 bundle 目录到 artifact staging，再对复制件重新执行 `verify_run_bundle`；报告记录 nominal/injected 两份 bundle 的归档路径。
- 对 Chromium：保留浏览器实际下载的 ZIP 原件，不仅验证临时下载；将其复制到 artifact staging，并生成 sidecar，记录 ZIP SHA-256 与 bundle manifest SHA-256。
- Artifact 上传必须显式包含物理 run_bundles/**、browser_run_bundles/**、JUnit 与 effect_ab.json，并保持 `if-no-files-found: error`，避免路径写错时静默产出不完整交付包。
- 归档代码已加入候选分支；只有同头官方 CI 完成且 Artifact 内核对 10 份物理 Run Bundle、9 份 Chromium ZIP 与 9 个 sidecar 后，O3-D 才可记为 PASS。

## O3-C 高级参数浏览器控件修复（2026-11-19）

- 首轮 PR #190 native workflow `44455172008` 的浏览器 job 失败，10 条 whole-spacecraft event Run journey 都在等待 `simulation.step_s` 可见时超时；其他 12 条测试通过、4 条按条件 deselected。此处不是物理模型失败。
- 根因：`simulation.step_s` 在 Capability Form Schema 中是 `importance=advanced`。测试直接给 DOM 内 `<details>` 赋 `open=true`，没有使用产品的参数显示模式切换，导致高级控件在向导参数面板状态变化时仍不可见。
- 修复后的真实 UI 操作顺序：展开 `#wizardParametersPanel` → 点击 `[data-wizard-parameter-mode="all"]` → 等待 `simulation.step_s` 输入可见 → 修改积分步长。这样验证的是用户真实可执行的交互路径，而不是绕过 UI 强行修改隐藏 DOM。
- 修复提交 `cca76913d01f7758fb6de13fa194715f32be6c84`。新 head 的完整 Chromium 与原始 Run Bundle 归档验收尚未完成；不将测试代码修复记作 O3-C PASS。
