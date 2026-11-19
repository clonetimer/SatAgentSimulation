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


## O3-A 合并与 O3-B 初始审计经验（2026-11-19）

- O3-A PR #188 已合入 main：`a96d7feddbb051cf0cf590a3f8c5e4679eed348a`。同 head 的 P9-G 浏览器回归 20/20 PASS；desktop/mobile 截图与 JUnit 已归档。
- Whole runtime 的 `external_or_proxy_module_count` 不是 0 的原因已核实：4 个 engineering-proxy power bridges（ADCS、payload、comm、heater）；启用推进再增加 propulsion power bridge。它们的来源边界在 `model_source_boundary.engineering_proxy` 中明确列出，不能将其误称为官方 Basilisk 模块。
- O3-B 的事件效果本身由 Basilisk 官方模型与项目原生 SysModel 在同一 Process/Task 中执行；证据需分别记录官方模块、项目原生模块和 engineering proxies 数量，不能只看 runtime truth 字符串。
- RW1 默认初始轮速约 10.47 rad/s，低于 speed-limit 测试阈值 15 rad/s；如果不显式设置 `rw_initial_speed_rad_s_0=100`，事件虽会更新 limit 字段，却未必真正触发超速制动。A/B 必须验证 active torque 的方向与 wheel speed 响应。
- 推进 impulse-loss 改变的是 burn command on-time，而不是 thruster 的瞬时 thrustFactor；不能把 `propulsion.max_thrust_factor` 误当成冲量比例。正式证据应记录 burn controller 的实际 OnTimeRequest，并用燃料消耗和轨道响应证明物理结果。


## O3-B 首轮实现经验（2026-11-19）

1. Whole-spacecraft event effect 不能只复用 subsystem 测试：必须在 whole unified Process/Task 中运行同一 TaskSpec nominal/injected，封存两个 Run Bundle 并比较直接 recorder。
2. 为控制 CI 成本，6 个 ADCS 事件共享一个相同初始状态的 nominal baseline，2 个推进事件共享一个 propulsion-enabled nominal baseline；8 个 injected run 独立封存，总计 10 次 Basilisk Run。
3. RW speed-limit 测试必须让初始轮速超过 limit。默认轮速约 10.47 rad/s，而用户配置的 15 rad/s 限值不会触发制动；本轮将 RW1 初始轮速设为 100 rad/s，并让限速事件从 t=0 生效，确保有超速制动 recorder 证据。
4. Propulsion impulse-loss 不是瞬时 thrustFactor 衰减，而是 one-shot burn controller 缩短 OnTimeRequest。不能用 `propulsion.max_thrust_factor` 代表剩余冲量比例。本轮增加官方消息 recorder `propulsion_burn_command`，逐样本记录 `OnTimeRequest`，并用 fuel mass、thrust、orbit radius 的 A/B 证明物理响应。
5. Propulsion 1 s injected burn 使用 0.2 s sample interval，避免 1 s sample 恰好落在 burn 结束边界而漏掉短燃烧窗口。
6. Whole-spacecraft runtime 包含 4 个 engineering-proxy power bridges，推进启用时为 5 个。它们不等于 external solver，也不是官方 Basilisk 模块；测试必须记录并断言该数量，不可把整个整星运行图宣传为全官方模块原生。
7. 8 个新 profile 与 operator contract 的 evidence_fields 做逐项一致性检查；guided event allowlist 仍保持 6 个 O3-A 已验收事件，等 O3-B 物理 A/B 通过、O3-C 浏览器旅程通过后再扩展。


## O3-B 证据契约与采样回归修正（2026-11-19）

- 全部 whole-spacecraft operator evidence_fields 与 VERIFIED_NATIVE_EFFECT profile 做逐项 parity 后，发现两个历史契约不一致：downlink profile 将 recorder 字段误写为 `comm.downlinked_bits`，实际 trace 是 `data.downlinked_bits`，同时 operator 与 profile 对 `data.storage_bits` 的覆盖不同；已统一为两者都声明 storage backlog 与 delivered data。solar efficiency profile 补齐 operator 已声明的 `eps.battery_soc`。
- formal effect test 现在对 whole-spacecraft 14 个事件逐项断言 operator evidence_fields 与 profile evidence_fields 的集合一致，避免只验证 profile 存在而字段契约漂移。
- Gyro noise baseline 不假设为零；IMU 可能持续输出基础噪声。A/B 使用固定 seed，对比活动窗口最大噪声幅值相对 nominal 至少 20 倍，并在事件结束后与 nominal 对齐。
- RW jamming 和 speed-limit 场景从 t=0 生效，避免轮速在事件开始前被控制器自然改变而错过制动触发；speed-limit 测试使用初始 100 rad/s、限值 15 rad/s。
- Propulsion impulse-loss 的 on-time 只有 1 s；propulsion Run Bundle 使用 0.2 s recorder 采样，避免 1 s 采样恰好错过燃烧活动窗口。
