# P9-O1：EPS 与 Comm/Data 分系统工作台开发经验

日期：2026-11-19  
状态：候选实现中；最终准入以同一 PR head 的官方 Basilisk 与 Chromium 证据为准。

## 代码审计结论

本轮没有必要新增仿真 adapter 或重新实现 Basilisk Process/Task：
- EPS 已有 subsystem.eps.unified_native.v1、EpsUnifiedNativeAdapter、eps_unified_native 模板、官方 Basilisk 电源节点与项目 PDU。
- Comm/Data 已有 subsystem.comm_data.unified_native.v1、CommDataUnifiedNativeAdapter、comm_data_unified_native 模板、官方 Basilisk Instrument/Storage/Transmitter。
- 后端已有 sealed Run、多速率原生 recorder、FMEA 和部分 API 测试。
- 实际产品缺口在前端白名单：NATIVE_SUBSYSTEM_TEMPLATE_BY_OBJECT 仅有 ADCS，导致 EPS/Comm/Data 后端存在、用户却不能从快速仿真的分系统入口选择。

## 本阶段改动

1. 在统一向导注册 EPS 与 Comm/Data 的 capability/template 映射，复用 Scenario Template API 和原有 TaskSpec 投影。
2. 把“分系统独立仿真”说明更新为 ADCS、EPS、Comm/Data 三个已准入对象。
3. EPS 的来源提示明确区分官方 Basilisk 电源模块和项目原生 PDU。
4. Comm/Data 的来源提示明确只覆盖 Instrument、StorageUnit、Transmitter，不冒称地面站过站或 RF/BER/PER 闭环。
5. 保留 N5-E 的事件权限隔离：只有 ADCS 有经过物理 A/B 与服务端 allowlist 的五类事件；EPS/Comm/Data 的事件面板继续隐藏。
6. 新增真实 Chromium EPS/Comm/Data 运行、结果、历史回放和完整 Run Bundle 下载验收。
7. 扩展多速率/FMEA API 测试，使两个分系统分别执行 sealed Run、读取 streams/FMEA 并校验完整 Run Bundle。

## 关键工程经验

1. **后端已有能力时，先找用户路径缺口，不要重建 runtime。** 注册模板、adapter、Process/Task、Run Bundle 和 recorder 已经存在；本阶段只补齐 UI 入口和证据闭环。
2. **Capability 存在不等于用户向导准入。** 只有列入 NATIVE_SUBSYSTEM_TEMPLATE_BY_OBJECT 并通过真实浏览器旅程的对象才会显示。
3. **分系统的模型来源边界必须写在用户入口。** EPS 的 PDU 是项目原生模型；Comm/Data 的三类数据节点是官方 Basilisk 模块。不能统一笼统地称为“全官方”。
4. **事件能力逐分系统独立验收。** N5-E 的 ADCS allowlist 不得扩散到 EPS 或 Comm/Data；未完成各自 A/B 的事件面板保持隐藏。
5. **多速率必须来自原生 recorder。** interpolation_used=false，不能用运行后抽样伪装为原生多速率。
6. **产品闭环要包括历史与下载。** API sealed PASS 只是中间门槛；真实 Chromium 还要下载完整 ZIP、逐文件校验哈希并重开同一 Run。
7. **pytest 选择必须有证据。** 对新增浏览器测试增加 collect-only 精确命中检查；同时记录实际 passed/deselected，防止工作流绿色但目标测试没有执行。

## 当前验收状态

- 前端映射与说明已实现。
- API 测试已扩展为 EPS / Comm/Data 两个 sealed Run Bundle。
- Chromium 真实旅程已加入同一官方 Basilisk workflow。
- 待同一 PR head CI 完成后，按真实日志补充 PASS 数量、artifact ID 和 merge commit；未通过前不标记正式交付。