# P9-O2：EPS 与 Comm/Data 原生故障/退化效果准入

日期：2026-11-19  
基线：P9-O1 merge commit `5f61e73aa4cdb952ee927090f6c995255088984b`  
状态：P9-O2 A/B/C/D/E 已完成并合入 main。EPS 与 Comm/Data 的四类受控事件已通过官方 Basilisk 2.11.0 配对 sealed Run Bundle、formal effect evidence 与六条真实 Chromium 用户旅程；交付证据归档于 Artifact `14484456607`。

## 主线目标

在 P9-O1 已开放 EPS、Comm/Data 正常离线分系统 Run 的基础上，逐项验证故障/退化是否真正改变 Basilisk 原生运行图中的物理量。只将有直接 recorder 证据、可复现 A/B、封存结果和浏览器旅程的事件纳入工作台 allowlist。

本阶段不扩展为整星联合故障诊断，不引入新求解器，也不把组件库中所有故障类型直接开放给用户。

## O2-A：修正效果语义与参数合同

1. Comm/Data 当前 `link_or_storage_failure` 实际只验证 SimpleTransmitter 数据率变化；改为准确的 `transmitter_rate_loss`。不再把 nodeBaudRate 误称为 RF 功率放大器故障。
2. Comm/Data 当前 `margin_or_throughput_degradation` 实际只验证发射机数据率下降；改为 `transmitter_rate_degradation`。
3. 对旧的宽泛 ID fail closed；不允许 UI 名称、capability 声明和 adapter 实际注入对象不一致。
4. EPS 保留两个明确的组合场景：`generation_storage_distribution_failure` 与 `energy_margin_degradation`。用户说明必须明确这是受控组合场景，不是任意单点故障菜单。
5. TaskSpec 的 `magnitude`（归一化严重度） 不得被静默忽略：连续乘性效应从标称系数 1.0 向故障系数插值，加性/比例退化效应线性缩放，时间函数退化乘以事件 severity；override/clamp 等二值或绝对操作保持离散语义。severity 必须为有限 [0,1] 数值。

## O2-B：官方 Basilisk 2.11.0 配对物理 A/B

每种事件都必须有相同参数、相同仿真时长/步长的 nominal 与 injected 两个正式 TaskSpec Run：

- Run 状态 SUCCEEDED；
- validation PASS，sealed=true，integrity.ok=true；
- official Basilisk 模块和 project-native 模块数量与 capability 边界一致；
- runtime injection：actual application > 0、failed direct application = 0、unsupported = 0；
- 故障在结束时恢复原始模块参数；退化在起始后持续体现；
- 直接 recorder 字段必须证明效果，不以标签变化、注入计数或派生曲线单独作为物理证据。

### EPS

- 故障组合：电池有效容量、太阳阵输出及 PDU/负载路径的受控组合效果；分别核对原生 recorder 和项目 PDU 的实际注入记录。
- 退化组合：电池储能能力、太阳阵输出及配电/负载路径的受控组合退化。
- PDU/负载切除属于项目原生 SysModel；不得把它们计为官方 Basilisk 模块。

### Comm/Data

- 发射机数据率损失：在官方 SimpleTransmitter 的 nodeBaudRate 输入上施加受控故障，使用原生存储节点 recorder 验证数据率下降并在事件结束后恢复；不宣称 RF 功率损失。
- 发射机数据率退化：验证原生下传速率随时间持续下降及星上存储积压增加。
- 不宣称地面站可见性、轨道过站、天线指向、RF 链路几何、BER/PER 闭环或存储坏块物理模型。

## O2-C：Effect Evidence 与服务端准入

- 为四个 effect 建立 `VERIFIED_NATIVE_EFFECT` profile。
- 每个 profile 至少绑定一个直接 recorder 字段及其 nominal 参数参照；不得只断言 `label.*_active`。
- 将事件 ID、事件 kind、capability ID 与 evidence profile 精确匹配。
- 保持 `operator.effects[].verification` 的 schema 值为 `declared`；原生物理准入只由 effect evidence registry 的分类与 runtime assertions 表示。
- 对未知 ID、旧宽泛 ID、severity 越界和未支持事件 fail closed。

## O2-D：真实 Chromium 用户旅程

EPS 与 Comm/Data 各执行 nominal、fault、degradation 三类用户旅程：

1. 从“分系统独立仿真”选择对应对象。
2. 事件面板只展示该对象的已验收事件；不能看到其他分系统的事件。
3. 选择事件、调整开始时间/持续时间/severity，执行服务端 TaskSpec 校验。
4. 在真实 Chromium 中提交异步 Basilisk Run，等待 SUCCEEDED。
5. 检查 validation/effect evidence、直接遥测、曲线和 FMEA。
6. 下载完整 Run Bundle，核对 manifest/seal 和逐文件 SHA-256。
7. 从 Run Center 重开同一个 Run，确认历史来源一致。

## O2-E：CI 与经验归档

- 扩展官方 Basilisk 2.11.0 native workflow，执行四个配对 A/B 的 sealed Run Bundle 测试。
- 扩展 Chromium workflow，使用 collect-only 精确断言确认六个目标旅程均被收集；记录 passed/deselected 数量。
- 物理与浏览器证据必须来自同一最终 PR head。
- 更新开发经验、组件能力盘点与当前状态报告；保留实际 Artifact ID、SHA-256、测试数量和 merge commit。
- 任一事件物理效果失败时，只关闭该事件，不阻塞其他已通过事件；不能用其他事件通过替代。

## 明确不在本阶段

- EPS/Comm-Data 与 ADCS 的跨分系统同图联合仿真；
- 整星实时/连续仿真；
- EPS 的任意 PDU 电气故障、母线短路、真实保护器件热失效；
- Comm/Data 的地面站过站、天线指向、RF 几何、BER/PER 和存储坏块/数据完整性闭环；
- 未经独立 A/B 的其他 capability fault/degradation 类型。

## 准入原则

只有 capability 声明、adapter 实际注入、direct recorder、effect evidence profile、sealed Run Bundle 和 Chromium 用户旅程六者一致，事件才可出现在普通用户向导中。

## O2-A 当前实现记录（2026-11-19）

- 新增 src/subsystems/event_severity.py，统一读取 TaskSpec magnitude（兼容 UI normalized magnitude），并对连续 ParameterEffect 做正确缩放；离散 override/clamp 保留二值/绝对语义。
- EPS adapter 已在执行前校验 severity，并将其应用到组合故障的连续效应与时间函数退化；组合场景仍未拆成单点故障。
- Comm/Data 将宽泛且失真的两个 ID 改为 transmitter_rate_loss / transmitter_rate_degradation。两者都只作用于官方 SimpleTransmitter 的 nodeBaudRate 输入，trace 增加 nominal 参照值；没有将该量称为 RF 功率或链路裕度。
- Comm/Data 的故障 severity A/B 预期：severity=0.8 时保留率 0.48，severity=0.4 时保留率 0.74；故障结束后恢复标称速率。实际结果以官方 Basilisk CI 为准。
- 旧 Comm/Data 宽泛事件 ID 已 fail closed；后续必须先通过 A/B 与 sealed Run Bundle，再添加 effect evidence profile，最后才考虑浏览器入口。


## O2-B 首批成对 Run Bundle 测试（2026-11-19）

已新增 tests/test_native_eps_comm_data_effects_bundle.py，针对 EPS fault/degradation 与 Comm/Data rate fault/degradation 四个事件分别运行 nominal/injected TaskSpec 配对。每次运行要求官方 Basilisk 2.11.0、SUCCEEDED、sealed、integrity PASS、validation PASS、runtime injection 直接应用证据和 recorder 物理差异；故障另检查事件结束恢复，退化检查持续效果。

该测试已加入官方 native workflow，并在 PR #187 当前 head b57692ad289c84166954365a2de442543d7cc04a 上运行。尚未收到该 head 的最终结果；未通过前不进入 O2-C，也不开放用户事件入口。


### O2-B 首次失败与修复经验（2026-11-19）

首轮 sealed Run Bundle 测试 4/4 在 canonicalization 阶段失败，原因不是 Basilisk 物理模型，而是 TaskSpec 的 BaseEvent 严格 schema 使用 magnitude（[0,1]），不接受额外 severity 字段。修复后测试改用 canonical magnitude；adapter helper 继续兼容显式 severity，仅供直接 adapter 集成调用。必须以 canonical TaskSpec schema 为准，不能把内部语义字段直接写进用户任务文档。


## O2-C evidence profiles and PDU causal binding (2026-11-19)

- Added four effect-specific VERIFIED_NATIVE_EFFECT profiles for EPS aggregate fault/degradation and Comm/Data transmitter rate fault/degradation. Profiles assert direct recorder fields against nominal references, with explicit claim limitations.
- Added explicit operator observability.effect_evidence bindings and formal runtime tests for all four profiles; CI now runs the full formal runtime suite rather than only checking profile registration.
- The EPS PDU trip was previously described by an unused trip_state parameter. The native PDU SysModel now owns a scheduled channel_enabled state and applies it to the payload command gate; the combined EPS fault targets this real field.
- A PDU trip sheds payload load, so net_power_w can become less negative. A former assertion that every EPS fault must lower net_power_w was physically incorrect. Tests now assert direct payload load cut-off and the PDU shed reason instead.
- The latest regression run exposed that assertion error; corrected tests are on the current branch, and their official Basilisk re-run is pending. O2-C is not yet promoted until formal runtime profiles pass on one final head.


### PDU 保护动作与事件结束语义（2026-11-19）

- PDU trace 现逐样本记录 payload_channel_enabled 与 shed_reason，并通过 EPS adapter 输出 eps.pdu_payload_channel_enabled / eps.pdu_shed_reason。
- 故障 active 时要求通道关闭、payload 供电为零且 shed_reason 含 payload_channel_trip；故障结束后要求通道恢复开启且 trip 原因消失。若 SOC 仍低于阈值，PDU 可以继续按 payload_low_soc 保护性切除负载，不强制负载立即恢复。
- 当前最终同 head 官方 Basilisk A/B 与 formal evidence 仍在重跑；通过前不进入 O2-D 开放向导。


## O2-A/B/C 正式准入结果（2026-11-19）

- 官方 workflow `Independent native component and subsystem physical and Run Bundle acceptance`：run `44411927773`，结论 success，测试代码 head `2e7ceb66203dbcfab49509bb043109cc9f19f947`。
- EPS fault/degradation 与 Comm/Data transmitter-rate fault/degradation 的 nominal/injected sealed Run Bundle 配对测试：4/4 PASS。
- Formal Effect Evidence runtime：17 passed、1 deselected；effect profile registration：1 passed；EPS/Comm-Data guided catalog：2 passed。
- 该 workflow 的 Chromium 部分因本次代码改动尚未触及 web surface 而 skipped；这不代表 O2-D 已通过。
- 正式回归修复：从 formal test 的 outputs.qoi 请求中排除 label.* 派生标签（仍保留在运行 trace/evidence）；官方 native workflow 补装 pyproject 已锁定的 numba==0.63.1，以满足 whole-spacecraft thermal quadrature 真实依赖。
- 2e7 之后有仅文档变更的提交；O2-D 将修改 web/app.js 与 browser tests，产生新的代码 head，并在该 head 重跑完整 CI。


## O2-D Chromium 用户旅程实施中（2026-11-19）

- 普通分系统向导现在仅在 ADCS、EPS、Comm/Data 各自具有已验收 event_catalog 时显示事件编辑区；单机 native 入口仍保持 nominal-only。
- EPS allowlist 精确限制为 `generation_storage_distribution_failure` fault 与 `energy_margin_degradation` degradation；Comm/Data 精确限制为 `transmitter_rate_loss` fault 与 `transmitter_rate_degradation` degradation。前端 addEffect 再做一次 capability + event kind allowlist，服务端 TaskSpec / effect evidence 继续作为最终准入。
- 更新分系统选择后的能力边界说明，明确 EPS 为受控组合故障，Comm/Data 只验证 SimpleTransmitter.nodeBaudRate，不扩张为 RF/链路模型。
- 原有两条 nominal Chromium 旅程扩展为六条 parameterized journey（EPS/Comm-Data 各 nominal/fault/degradation），增加事件面板隔离、时间窗与 magnitude 编辑、正式证据 PASS、直接 telemetry、曲线选择、FMEA、Run Bundle seal/hash 与 Run Center 同一 Run 重开检查。
- CI collect-only 已从“至少找到测试名”提升为精确要求六条目标旅程全部收集。最新 browser workflow 正在对新 head 运行；在六条实际 Chromium 旅程全部 PASS 前，不宣布 O2-D 完成。


## O2-D 首轮真实 Chromium 结果与 O2-E 证据归档（2026-11-19）

- 官方 workflow run `44415420726`，PR head `7e991a3c4641241c38f18423f7ecce130f24ca4f`，结论 success。Chromium 实际执行步骤 22：`12 passed, 4 deselected`；其中 EPS/Comm-Data 六条参数化用户旅程全部被精确收集并在真实 Chromium 中通过（EPS nominal/fault/degradation；Comm/Data nominal/fault/degradation），另有 ADCS 与既有单机旅程回归通过。
- 六条目标旅程逐条执行：向导对象选择、故障/退化白名单隔离、时间窗/magnitude 编辑、正式 TaskSpec 校验、异步 Basilisk Run、validation/effect evidence、直接遥测与曲线、FMEA、sealed Run Bundle 下载和逐文件 SHA-256、Run Center 同一 Run 重开。
- 本轮原 artifact `native-component-physical-evidence` ID `14480197134`，SHA-256 `42c906c993499f6a365d4919d5d20a39735dad5760b74ddd38ef0c1720735c9a`，大小 38,264 bytes。原 artifact 包含 O2-B / O2-C JUnit 与结构化 A/B 证据，但未包含 O2-D browser JUnit；因此不把该 artifact 作为最终 O2-E 完整归档。
- O2-E 补充：CI 现在将浏览器测试输出到 `reports/native_subsystem_eps_comm_data/guided_browser_journeys.xml`，并在上传前断言 JUnit 中恰有 6 条 EPS/Comm-Data 旅程和 1 条 ADCS 旅程，且无 failure/skipped；同一 artifact 将包含该报告。此补强提交后必须在新 head 重跑完整官方工作流，最终 artifact ID/SHA 以新 head 为准。


## P9-O2 正式收尾（2026-11-19）

- PR #187 `P9-O2: native EPS/Comm-Data event evidence and guided journeys` 已合并，merge commit `7207244a660db732ea321a0c9c64b28df0912c08`。
- O2-A：TaskSpec magnitude 严重度语义、EPS PDU 真实通道注入、Comm/Data 事件准确命名与 native SimpleTransmitter.nodeBaudRate 映射完成。
- O2-B：官方 Basilisk 2.11.0 下四组 EPS/Comm-Data nominal/injected sealed Run Bundle 配对 4/4 PASS；故障恢复、退化持续性、direct recorder 与 integrity 均通过。
- O2-C：四个 `VERIFIED_NATIVE_EFFECT` profiles 与 operator observability evidence bindings 完成；formal runtime 17 passed、1 deselected，EPS/Comm-Data guided catalog 2 passed。
- O2-D：六条真实 Chromium 用户旅程（EPS nominal/fault/degradation，Comm/Data nominal/fault/degradation）已纳入官方 native workflow。PR #187 的浏览器用户旅程在合并前通过；随后 O3-A 同头回归 workflow `44424648554` success，JUnit 共 13 tests、0 failures、0 skipped，其中包含 O2 六条旅程与 O3-A 整星目录测试。
- O2-E：最终可移交证据 Artifact `14484456607`（`native-component-physical-evidence`，39,263 bytes，未过期），由官方 Basilisk 2.11.0 workflow `44424648554` 上传；O3-A 验收包复用并保留 O2 六条 Chromium 旅程的 JUnit 证据。PR #187 的合并 diff 同时保留 O2 原始代码、测试、capability/evidence profile 与开发经验。
- 边界保持：EPS 故障为受控组合场景；Comm/Data 只验证 SimpleTransmitter 数据率，不扩张为 RF 功率/链路/BER/PER/存储故障模型；不声称整星跨分系统联合故障闭环。
- 下一条主线已转入 P9-O3：整星统一运行图 14 类事件的独立物理证据与真实 Chromium 用户旅程；O2 不再重复开发。
