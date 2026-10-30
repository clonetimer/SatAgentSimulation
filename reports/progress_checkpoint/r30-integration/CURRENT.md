# 当前进展入口：整体资格尚未闭合

本页是后续追加的当前状态。原 README、snapshot-status 和初始78文件 manifest 是历史快照，其中的 PENDING 和创建阶段文字不再代表当前终态；原件及摘要没有重写。本包用于公开进展审阅，不是最终可移植发布，也没有向正在耐久的 root 应用运行时覆盖。

## 当前结论

| 阶段 | 已有证据 | 尚未闭合的范围 |
|---|---|---|
| R30 App9539 | 两个实际模型 API 案例47检查、wheel59检查、浏览器37检查分别通过 | 全套终态2267 PASS / 7 FAIL / 6 SKIP，未重判 |
| R32 | 六个测试输入修订，21个不同节点通过，生产 App 仍为9539 | 定向21不能替代全套 |
| R33 | 三个依赖声明、marker 检查和 Python3.10 transport-only echo 通过 | 安装态缺命令矩阵资源的真实失败保留 |
| R34 App3e7d / Core89e3 | 安装态资源和默认 WS 独立证据通过；15秒 pace 烟测31条完整 | 全套终态2276 PASS / 1 FAIL / 6 SKIP / 2 WARN，共2283项，1365.10秒 |
| R35 GUI | 已发现 canonical→form→graph 导出丢失 solver、provenance、assumptions、pending | 修复中，GUI 来源及 solver 保真尚未闭环 |
| R36 依赖安全 | R34 唯一全套失败指向旧88项审计与新依赖集合不一致；root 的新90项审计另发现 Tornado 问题 | 修复和新安全审计尚未完成 |

不同 App、API、wheel、GUI 和独立复核的计数不相加为同版样本数。原 R20 全套2152 PASS / 12 FAIL / 6 SKIP / 2 WARN 及 R24 计划绑定失败也保留。

## R30 后续已通过项的准确范围

- [API47派生摘要](current-evidence-v1/api47.summary.json)：create17、refine42 两个实际新模型 API→脚本→原生案例，22+25项检查；**不是 GUI 验证**。同两案例的48项独立复核原件 SHA 为 `25e905da0a3297f76dfe03f94e9eb98a0c0512917289992073e3fc39c8650274`，索引也在摘要内。这不是95个模型样本。
- [wheel59派生摘要](current-evidence-v1/wheel59.summary.json)：安装 wheel 的 CLI、导出、原生和 HTTP 有限验证；**未开启 interactive**，不能覆盖后来发现的 WS 和资源安装缺口。
- [浏览器37派生摘要](current-evidence-v1/browser37.summary.json)：实际浏览器、HTTP 任务编辑和原生流程；**没有模型生成请求**，不能替代 GUI 新模型字段保真验收。

三个摘要均绑定 App `953972755f4188ae449ae29182bbe7963c429a5c41e3c19cbcb5e78af07eba1f`、Core `137dcf2341765077aaace82352bb03f019352cdb3032cb0dcbb3d0cd5e635b9a`，保留真实私有原件的相对路径和 SHA，并明确是派生摘要。原始运行没有因整理而重判。

## 增量、GUI 与短烟测

[R32–R34有限增量](deltas/r32-r34-v1/README.md)包含13个有序覆盖步骤、12个不同目标路径，以及6个历史指纹支持文件。R34 新增15秒原生烟测使用指定 installed API 解释器运行 **R34 源码**，完整保存31条 pace 观察，与终态总计数一致、零缺口、封存为 SEALED。它是 SHORT_15S，不是安装 wheel 代码的15秒验收，也不是完整30分钟或24小时通过，见[短烟测摘要](deltas/r32-r34-v1/evidence/r34-short15s-pace.summary.json)。

实际 GUI 的原4个 payload 和3张已看图位于 [GUI证据目录](evidence/gui-r30-v1/source-public-review-index.json)，其中保留已发现的字段保真缺口。R34 安装态的[独立脱敏交叉核对](evidence/r34-installed-independent-v1/summary.json)是对原证据的复核，不重复算作新网络运行。

原 root4c 的真实24小时实例仍在运行，root 报告漂移峰值为2.854秒。全天30分钟 P95 不能从旧4096条滑窗补推。新正式24小时尚未启动，不能继承原实例时长。

## 原始字节前像与指纹勘误

**相同 Git commit 不等于相同 raw App。** root 只读核对879个 App 输入，确认6个本机生成 egg-info 文件未追踪；另有839个工作区原始字节与 HEAD blob 不同，主要涉及 CRLF/LF。本准备者没有逐项证明这些差异全部仅为换行。补齐6个文件，也不能无条件声称 fresh clone 能跨 checkout 策略复现旧指纹。

当前仅保证“已有、完整匹配 Root4c 原始字节前像 + 指定 overlay”可以重建候选。必须先匹配整体 App 和每个 before SHA；任何前像不匹配都应停止，不能静默统一换行、强制覆盖，或用 commit 相同代替字节验证。没有扩大归档839个文件，因此本检查点不是最终可移植 release。

最新说明见 [source-preimage-erratum-v2.json](source-preimage-erratum-v2.json)，其范围补充已封存 delta 中第一次仅涉及6文件的勘误。6个 egg-info 文本只作为历史指纹支持，**不能用作安装 metadata 或依赖权威**；实际 wheel metadata 应从当前 pyproject 在独立 staging 中生成。

root 后来应用的5个非运行时治理 metadata 路径属于明确的 already-applied 状态，不能拿旧 before 值误报运行时 App 漂移。初始快照保留其生成时事实。

## 设计资料的有效入口

原[设计笔记](evidence/compact-workbench-design-note.md)保留原始字节，其来自 docs 目录的相对链接在复制后不适用。当前正确入口是[紧凑工作台原型](../../../prototypes/compact-workbench/README.md)和[实时界面复核](../../../prototypes/compact-workbench/realtime-review/README.md)。Carbon、Grafana 仅作为交互组织参考，不代表外部认可或完成视觉验收。

公开包只含明确白名单的代码、合成 fixture、有限文本支持和派生摘要，不纳环境、权重、provider 配置或全量原始遥测。有限检查不构成 secret-free 声明。准备者未执行 commit 或 push，由 root 审阅后决定推送。

本页及增量说明曾因 PowerShell stdin 编码损坏为字面问号，已在发布前直接以 UTF-8 修复。原稿及原摘要留私有归档，修复记录见 [encoding-repair-v1.json](encoding-repair-v1.json)。源码、测试与初始78文件 manifest 未改。
