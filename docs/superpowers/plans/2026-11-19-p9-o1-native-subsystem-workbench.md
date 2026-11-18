# P9-O1：EPS 与 Comm/Data 分系统独立仿真工作台

日期：2026-11-19  
状态：实施中；以 P9-N5 已合入主干为基线，扩展现有注册分系统入口。

## 主线判断

P9-N5 已完成 ADCS 独立闭环和五类受控故障/退化向导。下一步不重建仿真 runtime，而是补齐已存在的 EPS 与 Comm/Data 原生分系统从“后端可执行”到“普通用户可从对话框选择、运行、回放和导出”的产品闭环。

代码审计确认：
- EPS capability / adapter / template 已存在；官方 SimpleSolarPanel、SimpleBattery、SimplePowerSink 与项目 PDU SysModel 在同一 Process/Task 中执行。
- Comm/Data capability / adapter / template 已存在；官方 SimpleInstrument、SimpleStorageUnit、SimpleTransmitter 在同一 Process/Task 中执行。
- 两者已有 backend Run / 多速率 recorder / sealed Run 相关测试，但 NATIVE_SUBSYSTEM_TEMPLATE_BY_OBJECT 目前只映射 ADCS，快速仿真“分系统独立仿真”入口因此只显示 ADCS。
- N5-E 的 event authoring 逻辑只对 ADCS 提供 allowlist；EPS/Comm/Data 必须继续 nominal-only，不能借用 ADCS 的五类事件菜单。

## O1-A：向导入口与来源边界

1. 在 NATIVE_SUBSYSTEM_TEMPLATE_BY_OBJECT 增加：
   - subsystem.eps → eps_unified_native → subsystem.eps.unified_native.v1
   - subsystem.comm_data → comm_data_unified_native → subsystem.comm_data.unified_native.v1
2. 服务端返回 TaskSpec 必须同时满足 capability ID、simulation.level=subsystem、simulation.backend=basilisk、assurance.allow_proxy=false。
3. EPS 提示必须明确：PDU/负载切除是项目原生 SysModel；不宣称轨道日照/日蚀闭环或跨分系统耦合。
4. Comm/Data 提示必须明确：SimpleInstrument/StorageUnit/Transmitter 为官方 Basilisk；不宣称地面站可见性、轨道过站、RF 链路几何或 BER/PER 闭环。
5. 更新向导说明，明确 ADCS、EPS、Comm/Data 是三个独立 Run，不代表跨分系统联合闭环。

## O1-B：事件权限隔离

- ADCS 继续使用 N5-E 服务端 allowlist。
- EPS、Comm/Data 事件编辑面板继续隐藏，只开放 nominal finite/offline。
- 不因 capability YAML 中声明 fault/degradation，就自动把事件开放到用户向导；各分系统须独立完成物理 A/B、evidence profile、schema allowlist 和浏览器旅程。

## O1-C：sealed Run / API / 多速率验收

EPS 与 Comm/Data 各自通过正式模板与 Run Bundle：
- TaskSpec capability、Basilisk backend、allow_proxy=false。
- Run 状态 SUCCEEDED、validation PASS、sealed=true、integrity.ok=true。
- 官方模块数量和项目模块边界正确；external/proxy module count 为 0。
- 读取基础遥测、native recorder multi-rate streams、plots 和 FMEA。
- multi-rate stream ID 与模板一致，interpolation_used=false。
- 下载完整 Run Bundle，验证 ZIP 精确成员、manifest/seal 绑定和全部 SHA-256。

## O1-D：真实 Chromium 用户旅程

每个分系统分别执行：
1. 从快速仿真选择“分系统独立仿真”。
2. 只出现已准入的 ADCS、EPS、Comm/Data 三个对象。
3. 选择 EPS 或 Comm/Data，自动加载服务端注册模板；事件面板保持隐藏。
4. 校验 TaskSpec，真实提交 async Run，等待 SUCCEEDED。
5. 核验 provenance、sealed/integrity、遥测曲线、多速率 streams、FMEA。
6. 从结果面板下载完整 Run Bundle 并逐文件校验 SHA-256。
7. 从 Run Center 重开同一 Run，确认历史来源和曲线一致。

## O1-E：CI 与开发经验

- 同一最终 PR head 必须通过 official Basilisk 2.11.0 专项、EPS/Comm Data sealed API 测试、真实 Chromium 旅程和共享回归。
- CI 增加 pytest collect-only 精确测试选择断言；不能因 -k 拼写不匹配而静默跳过用户旅程。
- 专项证据归档到 workflow artifact。
- 不把单测/接口测试当成浏览器产品验收；不以工作流数量替代实际测试名、passed/deselected 数量。

## 不在本阶段开放

- EPS fault/degradation 用户事件菜单。
- Comm/Data fault/degradation 用户事件菜单。
- EPS—ADCS、EPS—Comm/Data 或载荷的跨分系统同图耦合。
- 地面站过站、RF 几何、BER/PER 闭环。
- subsystem realtime / continuous execution。

## 后续路线

O1 完成后，再分别评估 EPS 与 Comm/Data 的故障/退化物理 A/B，逐个决定是否建立独立 event allowlist。任何一个分系统的事件能力都不得自动继承另一个分系统的准入状态。