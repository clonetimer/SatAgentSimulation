# P9-O2：EPS 与 Comm/Data 原生故障/退化效果准入

日期：2026-11-19  
基线：P9-O1 merge commit `5f61e73aa4cdb952ee927090f6c995255088984b`  
状态：实施中；在同一最终 PR head 完成官方 Basilisk A/B、sealed Run Bundle、效果证据和 Chromium 用户旅程前，不开放事件入口。

## 主线目标

在 P9-O1 已开放 EPS、Comm/Data 正常离线分系统 Run 的基础上，逐项验证故障/退化是否真正改变 Basilisk 原生运行图中的物理量。只将有直接 recorder 证据、可复现 A/B、封存结果和浏览器旅程的事件纳入工作台 allowlist。

本阶段不扩展为整星联合故障诊断，不引入新求解器，也不把组件库中所有故障类型直接开放给用户。

## O2-A：修正效果语义与参数合同

1. Comm/Data 当前 `link_or_storage_failure` 实际只映射到发射机功率放大器故障；改为准确的 `transmitter_power_loss`。
2. Comm/Data 当前 `margin_or_throughput_degradation` 实际只验证发射机吞吐率下降；改为 `transmitter_throughput_degradation`。
3. 对旧的宽泛 ID fail closed；不允许 UI 名称、capability 声明和 adapter 实际注入对象不一致。
4. EPS 保留两个明确的组合场景：`generation_storage_distribution_failure` 与 `energy_margin_degradation`。用户说明必须明确这是受控组合场景，不是任意单点故障菜单。
5. TaskSpec 的 `severity` 不得被静默忽略：故障连续参数效应和时间函数退化均按事件 severity 缩放；离散开关/跳闸保持二值语义。输入范围必须校验。

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

- 发射机功率损失：验证 SimpleTransmitter 的原生存储节点下传速率下降，并验证故障结束后恢复。
- 发射机吞吐率退化：验证原生下传速率持续下降及星上存储积压增加。
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