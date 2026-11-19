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
