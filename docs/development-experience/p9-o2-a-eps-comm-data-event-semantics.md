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


## O2-C 与 PDU 物理语义回归经验（2026-11-19）

1. 证据分级：新增四个 VERIFIED_NATIVE_EFFECT profile，但 profile 注册不等于测试通过；必须由正式 Run Bundle 和 direct recorder assertion 驱动，并在 formal runtime test 中执行。
2. PDU trip 不能只记录 trip_state。若实际调度模型不读取该字段，runtime injection 即使报告应用成功也不会改变负载。现在将 channel_enabled 作为 PDU SysModel 的真实调度状态，UpdateState 将其纳入 payload_enabled 判定。
3. 保护动作可能改善部分系统级指标。PDU 切除 payload 后，EPS 净功率可能上升（负载减少），因此不能要求所有 fault 都使 net_power_w 更低。应按机理验证：电池容量下降、太阳阵输出下降、载荷供电被切断、shed_reason 指明 channel trip。
4. 不用单一 QoI 的方向代替故障机理证据；要把受影响模块的直接字段、保护逻辑输出和因果标签一起纳入验收。
5. 当前 PDU 修复与正式 profile 测试的 CI 尚在重跑；未通过前不开放用户向导。


## O2-B/O2-C 回归中的测试工程修正（2026-11-19）

- PDU 物理断言调整后，EPS 单元测试新增 pytest.approx 但遗漏 import pytest；官方 CI 明确报 NameError，已补导入。
- 自动重构 EPS paired A/B 的 fault/degradation 分支时产生重复 else，在 pytest collection 阶段触发 SyntaxError；已重写整个条件分支，避免继续做局部字符串替换。
- 连续退化在 start_s 的首个采样点可能尚未产生非零变化；若断言要求 active_all 每个样本都偏离标称，会错误否定正确的时间函数。退化 profile 改用 active_any，并由 O2-B paired Run Bundle 在中后段采样单独验证持续退化。
- 这些错误都属于测试/验收代码问题，不应误报为 Basilisk 物理模型缺陷。修复后仍必须在最终 PR head 重跑完整官方 workflow。


## O2-B 原生太阳阵参数映射修正（2026-11-19）

- paired Run Bundle 对照发现：EPS nominal/injected 的 solar_power_w 完全相同，说明虽然 SimpleSolarPanel 模块已实例化，但旧的 output_power_w -> nodePowerOut 映射并不适用于 SimpleSolarPanel。不能用“模块已实例化”推断某个参数已被实际改变。
- 核对 Basilisk SimpleSolarPanel 官方接口后，确认可写参数为 panelArea、panelEfficiency、nHat_B；setPanelParameters 同时设置前述构型参数。已将 string_availability 映射到 panelArea，将 cell_efficiency/output_power_w 映射到 panelEfficiency（SimplePowerSink fallback 仍保留 nodePowerOut 回退）。
- EPS adapter 增加 solar_panel_area_m2、solar_panel_efficiency 及其 nominal 对照字段；sealed A/B 现在要求确认使用官方 SimpleSolarPanel，并直接比较面积/效率参数，而非只看功率曲线。
- EPS fault profile 验证 panelArea 下降；EPS degradation profile 验证 panelEfficiency 下降。Comm/Data 与 EPS fault profile 的 active 样本范围改为 active_any，避免在事件起始采样时刻尚未完成调度更新就要求每个样本都偏离标称。
- 最新官方 Basilisk 回归正在执行；在最终同一 PR head 全部 PASS 前，不把这轮映射修复记为通过。


### 原生太阳阵参数的时间采样边界补充（2026-11-19）

- 不能在仿真结束后读取 SimpleSolarPanel.panelArea/panelEfficiency 来代表事件期间状态：故障结束会恢复 panelArea，读取时已回到 nominal；时间退化参数可能保持最终值，但不是逐样本遥测。
- 因此最终验收使用官方 SimpleSolarPanel 的 nodePowerOut recorder 作为输出，并在事件开始前最后一个 recorder 样本建立同一 Run 内的 nominal_solar_power_w 参照。故障/退化期间直接比较太阳阵输出功率，避免使用理论配置值或仿真结束时的参数快照。
- panelArea / panelEfficiency 仍是运行时注入的实际参数目标；证据层以其真实输出结果为准，而不是将模块属性快照伪装成逐样本遥测。
