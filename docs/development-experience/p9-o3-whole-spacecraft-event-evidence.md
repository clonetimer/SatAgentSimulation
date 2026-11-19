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
