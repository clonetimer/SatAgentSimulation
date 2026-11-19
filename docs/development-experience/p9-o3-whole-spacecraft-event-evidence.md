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
