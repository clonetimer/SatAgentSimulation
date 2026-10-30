# R32–R34 有限进展增量：不代表整体验收通过

此目录在 R30 初始78文件快照之后新增，初始 manifest 与 snapshot 均保留。当前总入口为 [CURRENT.md](../../CURRENT.md)。本目录按 `overlay/r32`、`overlay/r33`、`overlay/r34` 顺序记录13个覆盖步骤，涉及12个不同目标路径；前像、后像和来源逐项见 [whitelist.json](whitelist.json)。

R32 包含六个测试输入修订；R33 包含三个依赖声明；R34 修复打包命令矩阵资源，其中 pyproject 以前一阶段 R33 字节为前像继续修改。所有覆盖文件均精确复制，未向正在耐久的 root 应用运行时覆盖，也没有执行 Git 或推送。

最终 R34 App 为 `3e7d9a064f1411183b7d7e0b7d692e9bb48d54bb8c92019aa8840c114414338b`，Core 为 `89e3d4dfc72613d5a200cd3fb58a7c9906c2dbf7019b7a5f5bc20ace984899d5`，共有1490个冻结输入。目标 manifest 位于 evidence 中；在**原始字节完全匹配的 R30 基线**上按13步更新，可得到记录的 R34 身份。不同 App 的组件结果不能加总为整版通过。

## 当前尚未闭环

R34 全套已终态：**2276 PASS / 1 FAIL / 6 SKIP / 2 WARN**，共2283项，1365.10秒。唯一失败是新依赖集合与旧安全审计集合不一致；root 另做90项新审计后发现 Tornado 问题，正在 R36 中处理。这次全套不能写成 PENDING 或 PASS。

R35 仍在修复实际 GUI 的 canonical→form→graph 导出丢失 solver、provenance、assumptions、pending 的问题。本增量不包含尚未封存的 R35、R36 代码。R33 安装态缺命令矩阵的真实失败保留；R34 安装态资源及默认 WS 的局部通过，不覆盖 R34 全套失败。

原 R30 全套2267 PASS / 7 FAIL / 6 SKIP、原 R20 全套2152 PASS / 12 FAIL / 6 SKIP / 2 WARN，以及 R24 计划绑定失败等旧记录均保留。原 root4c 的真实24小时实例仍在运行，root 报告漂移峰值2.854秒；全天30分钟 P95 不能从旧4096条滑窗推断。新正式24小时尚未启动。

R34 新增15秒烟测使用指定 installed API 解释器运行 **R34 源码**，完整保存31条 pace 观察，与 terminal 总数相同。独立 CLI 复核一致，原生封存为 SEALED。它是 SHORT_15S，不是安装 wheel 代码的15秒验收，也不是完整30分钟或24小时通过。全量 raw pace 和遥测留在私有证据目录，本包只公开来源 SHA 与明确标注的派生摘要。

## 历史指纹支持与重建前提

初始 README 中“d4加52文件可重建 App”的前提不完整。`application-source-v1` 还纳入6个本机生成、Git 未追踪的 egg-info 文本。其精确字节只归档到 `fingerprint-support/*.txt`，并记录原始相对路径及 SHA；PKG-INFO 中的通用路径示例没有静默改动原字节。

这些档案**仅用于核对历史源指纹，不得当作安装 metadata 或依赖权威**。实际 R33、R34 wheel metadata 从当前 pyproject 在独立 staging 中生成，另有实测。

六个文件仍不是完整的 fresh-clone 重建条件。root 后续只读核对还发现839个工作区原始字节与 HEAD blob 不同，主要涉及 CRLF/LF。因此，相同 commit 加覆盖文件、甚至补齐六个档案，也不能无条件保证跨 checkout 策略得到相同 raw App。只能在整体 App 及每项 before SHA 完全匹配时继续；不匹配必须停止，不能静默归一化换行或强制覆盖。详见[第二版原始字节勘误](../../source-preimage-erratum-v2.json)。本目录第一次 [fingerprint-erratum.json](fingerprint-erratum.json) 保留其当时范围，不能单独当作可移植发布证明。

root 后来已应用5个非运行时治理 metadata 修复；初始 before 值针对 d4 阶段，不应再断言这5个当前仍是 before，也不应据此判断运行时 App 漂移。

GUI 的4个原始 payload 和 R34 独立安装复核由另一审查代理分别放在顶层 `evidence/gui-r30-v1/`、`evidence/r34-installed-independent-v1/`，本目录不改写它们。

本包仅含明确白名单的源码、合成 fixture、有限指纹文本及脱敏派生摘要，不含环境、权重、provider 配置或全量原始遥测。只做有限路径与内容核对，不作 secret-free 声明；整体资格仍未闭合。

本说明曾因 PowerShell stdin 编码损坏为字面问号，现已在尚未发布时直接以 UTF-8 重写；原文与原 seal、manifest、whitelist 留私有归档。修复记录见 [encoding-repair-v1.json](../../encoding-repair-v1.json)，源码、测试和初始78文件 manifest 未改。
