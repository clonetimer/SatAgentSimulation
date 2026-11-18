# P9-O2-A：EPS / Comm-Data 事件语义与严重度：开发经验

日期：2026-11-19  
候选分支：feat/p9-o2-eps-comm-data-effects-ab-20261119  
基线：P9-O1 main commit 5f61e73aa4cdb952ee927090f6c995255088984b  
状态：实现已提交，等待官方 Basilisk 2.11.0 CI；不代表物理 A/B 已通过。

## 本阶段实际修改

1. 新增 src/subsystems/event_severity.py：
   - 统一读取 TaskSpec magnitude，优先显式 severity，兼容 UI normalized magnitude。
   - 拒绝非有限值和 [0,1] 之外的数值。
   - 连续乘性效应按 1 + (magnitude - 1) × severity 插值，保证 severity=0 时回到标称系数 1。
   - add、add_loss、degrade_multiply、grow_multiply、grow_add 按 severity 线性缩放。
   - override 和 clamp 保持二值/绝对目标，不把开关状态变成小数。

2. EPS adapter：
   - 校验 fault/degradation event severity。
   - 对组合故障的连续 ParameterEffect 应用 severity。
   - 对时间函数退化，将事件 severity 乘入退化模型自身 severity。
   - 保留原有两个受控组合场景，不宣称已实现每一种独立 EPS 故障。

3. Comm/Data adapter：
   - 删除失真的宽泛 ID link_or_storage_failure、margin_or_throughput_degradation；旧 ID fail closed。
   - 新 ID 为 transmitter_rate_loss、transmitter_rate_degradation。
   - 只对官方 SimpleTransmitter 的 nodeBaudRate 输入施加受控数据率故障/退化。
   - 记录 nominal transmitter rate 参照，便于对照 recorder 输出。
   - 明确不把 nodeBaudRate 下降称为 RF 功率放大器故障、链路裕度下降或地面站链路闭环。

4. 测试与 CI：
   - 新增纯函数 severity 语义测试。
   - Comm/Data 物理测试增加 severity=0.8 与 0.4 的数据率比例断言、故障结束恢复断言。
   - EPS / Comm-Data 测试加入非法 severity 拒绝检查。
   - 官方 Basilisk workflow 增加事件专项测试和 JUnit Artifact。

## 为什么这样改

- 先前 TaskSpec 中存在归一化 magnitude 字段，但 subsystem adapter 没有将其传递到实际 ParameterEffect / TimeFunctionDegradation，造成“配置可见、执行不生效”。
- 先前 Comm/Data 的 link/storage 故障 ID 实际只映射到 transmitter 侧，不能代表 storage failure 或 RF link outage。
- nodeBaudRate 是 SimpleTransmitter 的原生数据率输入，不是 RF 输出功率、天线增益、链路裕度或 BER/PER。效果命名必须服从可直接观测的物理量。

## 踩坑与防回归规则

1. 乘性效应的 severity 不能简单写成 magnitude × severity。若 magnitude=0.35、severity=0.8，直接相乘会得到 0.28；severity=0 时甚至把量乘成 0。正确的连续故障插值是 1 + (0.35 - 1) × 0.8 = 0.48。
2. override / clamp 是离散或绝对语义，不能线性插值成半开关。此类事件若要支持部分严重度，必须先定义新的连续物理量，而不是复用开关事件。
3. 不能将 rf_power_w 映射到 nodeBaudRate 后继续命名为发射功率损失；同一原生模块的不同字段不代表相同物理量。
4. 只看 label.fault_active 或 runtime injection count 不构成物理效果证据。仍须检查 recorder 直接字段、nominal 对照、故障结束恢复和 sealed Run Bundle。
5. 事件 ID 修改必须同步 capability modes、operator.effects、form label、baseline scenario、测试与工作流路径；否则 UI、API、验证器会出现不同版本的契约。
6. O2-A 的实现和测试代码提交不等于 O2-B 物理 A/B 通过。只有同一最终 PR head 的官方 Basilisk 2.11.0 测试与封存证据通过，才可升级为 effect evidence profile。

## 当前边界

- EPS 仍是两个受控组合事件，尚未拆成可独立选择的电池、太阳阵、PDU 单点事件。
- Comm/Data 只验证 transmitter nodeBaudRate 数据率输入；未建模 RF 功率放大器、天线方向图、链路预算、地面站过站、BER/PER 或存储坏块。
- EPS/Comm-Data 事件仍未在普通用户向导开放。
- 尚无 O2-B 的正式 paired TaskSpec sealed Run Bundle 证据，也未新增 VERIFIED_NATIVE_EFFECT profile。

## 下一步

1. 核对本分支官方 Basilisk 2.11.0 native workflow 的实际结果。
2. 修复任何失败项，不用模拟器替代真实 Basilisk。
3. 对 EPS fault/degradation 与 Comm/Data rate fault/degradation 分别生成 nominal/injected 成对 sealed Run Bundle。
4. 从直接 recorder 字段建立 effect evidence profile；不满足断言的单个 effect 保持关闭。
5. 最后补齐真实 Chromium 用户旅程和历史 Run Bundle 回放。


## O2-B 首批 sealed A/B 测试实施（2026-11-19）

- 新增 tests/test_native_eps_comm_data_effects_bundle.py，按四个 effect 分别运行 nominal 与 injected 的正式 TaskSpec Run Bundle。
- 每个 Run 检查官方 Basilisk 2.11.0、SUCCEEDED、sealed、integrity、validation、runtime injection 直接应用计数与 recorder A/B。
- 故障事件检查结束后恢复；退化事件检查在后续采样仍持续偏离 nominal。
- 重要边界：当前 profile 仍是 operator declared evidence；A/B 测试直接比较 recorder，但尚未创建 VERIFIED_NATIVE_EFFECT profile。O2-C 需基于最终 A/B 结果补齐断言。
- 当前 PR #187 head 为 b57692ad289c84166954365a2de442543d7cc04a，O2-B workflow 正在执行；结果未出之前不记录 PASS。


## O2-B 首轮失败记录与修复（2026-11-19）

- 首轮 paired Run Bundle 4/4 失败于 canonicalize_task_spec：BaseEvent 是 StrictModel，规范字段是 magnitude，severity 被判为 extra_forbidden。
- 这是测试契约错误，不是 Basilisk 执行失败；nominal Run Bundle 已先成功创建。
- 已将 sealed TaskSpec A/B 测试中的事件严重度改为 magnitude=0.8，并将 EPS/Comm-Data adapter 直测也统一改用 magnitude。
- event_severity helper 保留 explicit severity 兼容入口，但用户可提交的 canonical TaskSpec 仍以 magnitude 为准。
- 当前修复后的 CI 尚未完成，不能记录 O2-B PASS。
