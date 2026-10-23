# 持续卫星仿真 · Stable 主干集成候选验收

> 集成候选：`integration/stable-continuous-native-r9k-candidate`  
> 起点：R9-K 真实原生 Basilisk 单电池实验已通过的 PR #34；其余全部改动为 #3～#34 开发链。  
> **合并级别：源代码集成候选，不是整星中断恢复 Stable、飞行软件或可独立安装的完整离线发行包。**

## 1. 合并基线与风险

- 以 GitHub `main` 为基线创建**单个主干集成 PR**，使用完整分支差异执行验收，避免只验证堆叠 PR 的相邻差异。
- 提交前审计差异：本候选开始时相对主干 **415 commits / 115 changed files**。超过常规小型 PR，应经完整改动清单复核，并在 PR 中注明历史依赖关系。
- 旧的 #3～#34 保持记录可追溯；不能把单个 Draft 的 `mergeable=true` 当作发布批准。若集成 PR 需要额外修复，先在候选分支解决，不直接改旧的实验分支。
- **不将上游 Basilisk 自定义 C++ wheel 编译产物提交为仓库源码**。仅有实验性的 Actions 工件，必须验证 SHA-256、上游提交、补丁版本和 Python ABI；不可将 R9-K 单电池成果宣传为整星重启成功。

## 2. Stable 候选运行边界

| 功能 | 可作为候选交付的状态 | 强制限制 |
|---|---|---|
| 图形化设计 / 原有限时仿真 | 沿用原工程能力 | 与持续模式回归隔离 |
| 原生 Basilisk 统一整星持续运行 | Opt-in：显式 `continuous:true` | 真实动力学推进、遥控与遥测审计，不是循环回放 |
| 正在运行的**同一进程**控制操作 | 支持 start / pause / resume / step / rate / stop | 这里的 `resume` 是继续**仍存活的原生仿真进程**，不能称为断点跨进程恢复 |
| 命令日志与遥测 | 记录和审计真实执行 | 原进程退出后不得未经协调重复执行待处理 TC |
| 原生跨进程恢复 | 实验性原生状态子集 / 单电池研究用测试 | **禁止**以应用程序会话形式执行自动整星重启 |
| Basilisk C++ 电池导出/导入 | R9-K 用隔离定制 wheel 验证一个原生 `SimpleBattery`、固定电源输入、四步跨 PID 数值一致 | 无全 EPS 电源节点消息历史，无整星完整恢复 |
| 12-owner 联合恢复 | 仍为 **0/12 完整恢复** | 强制 `BLOCKED`；`automatic_resume_allowed=false`；`automatic_tc_replay_allowed=false` |

## 3. 集成验收矩阵

本候选 PR **相对 main** 的差异必须触发下列独立检查（检查结果以候选 HEAD SHA 为准）：

1. `continuous-satellite-native.yml`：原生 `bsk==2.11.0` 的统一整星与持续遥控回归；3RW/4RW、R9-F/G/H/I 原生偏差研究；8s/30s 实际墙钟；180s/3600s **仿真时间**长时测试。
2. `continuous-satellite-unit.yml`：控制面、持久化、命令日志、导出和恢复拒绝相关单元测试。
3. `continuous-satellite-static.yml`：Python/JavaScript/接口契约静态门禁。
4. `continuous-console-browser.yml`：实际 Chromium 中的持续仿真控制台页面和浏览器交互。
5. `native-battery-custom-wheel.yml`：确切 Basilisk 2.11.0 上游提交的 C++ 补丁定制 wheel 实际安装、SWIG ABI、非法状态拒绝、原生单电池跨进程四步数值一致。
6. `project-governance.yml`：脚本、依赖与工件管理检查。
7. `stable-continuous-integration.yml`：独立主干集成源包构建、实际安装、资产可见性、未授权跨进程重启的负面门禁。

**所有要求的 GitHub Actions 未全部完成成功前，不合并 main。** 合并后仍需用新主干 HEAD 检查下游 CI 状态和运行入口。

## 4. 安装和兼容性警告（Stable 前需人工确认）

当前 `pyproject.toml` 显式依赖 `bsk==2.11.0+satfix1`，而前述原生持续 CI 使用公开 `bsk==2.11.0`，R9-K 临时重编译出的 wheel 元数据版本也为 `2.11.0`。因此**源码包构建成功、`--no-deps` 安装成功不等于所有依赖可从公共索引自动解析**。

集成候选不能暗中把 `satfix1` 依赖改成公开版本，否则可能破坏原有整星模型的补丁依赖。发布正式可复制安装包前，需要：(a) 审核 `satfix1` 差异是否仍必需；(b) 固定 wheelhouse 来源、哈希与 ABI；(c) Windows 本地启动回归及操作说明验证；(d) 生成完整发行清单。当前阶段仍按项目 README 的离线 wheel/复用环境路径验收。

## 5. 主干合并决策

- **条件满足可以合并的是受门禁控制的源代码集成候选**，不是整星恢复产品化，也不是无人工确认的 Stable 二进制发行。
- 任何 CI 红灯、未触发的关键检查、未经审计的权限变更或恶化的原有限时仿真行为，均应拒绝合并。
- 整星恢复 `BLOCKED` 及禁止 TC 重放属于**禁止削弱的安全契约**；没有 12-owner 的实际原生导出/导入和不中断 vs. 新进程的整星长期数值一致证据，不得调整准入。
- 后续 EPS 电源网络所有者完整性追踪在 [Issue #35](https://github.com/clonetimer/SatAgentSimulation/issues/35)，不随本轮 Stable 源码候选自动关闭。

## 6. 独立验收入口

查看候选 PR 的 Checks 标签，必须看到源包、浏览器、原生、Windows、C++ wheel、控制面、静态和治理全部通过。

Windows 操作员仍先按 `START_HERE.md` / `README.md` 使用本地已有依赖。公开 wheel + 实验性定制 wheel 与 `satfix1` 的差异尚需整理，不提供无条件的一键恢复按钮。
