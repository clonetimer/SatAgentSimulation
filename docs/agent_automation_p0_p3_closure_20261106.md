# SatAgentSimulation 大模型自动化 P0～P3 阶段收尾报告

> 记录日期：2026-11-06（JST）。本报告是 Agent 自动化专项的**工程功能阶段验收**，不是整个仿真平台的 Stable、原生仿真全覆盖、真实模型效果或硬件资格验收。

## 1. 结论、版本与边界

- **结论：P0～P3 的约定工程功能已实现、对应 PR 已合并，选定远端门禁通过，可以进行 P0～P3 功能冻结。**
- P0～P3 代码完成点：`d51f14197503cff522ccf4412a0b0d7fe4f5fd1f`（PR #94 合入后的主干）。
- 阶段补充收敛点：`4d35ab5c77ca1f27013e0a529d25236fed607e02`（随后 #95 首答/执行归因与 #96 检索审计均合入后的主干）。
- 不是新 Stable release：`src/sat_sim/release_manifest.json` 当前仍标记 `status=IN_PROGRESS`；不借用历史测试、24 小时耐久或其他源代码版本的资格声明。
- 后续开发独立于本阶段：PR #97「GoalRun 目标实验工作台」已于 2026-11-06 作为独立后续功能合入 main（merge commit `7bbde7e`），**不纳入 P0～P3 的通过结论或冻结范围**。

## 2. 完成项与主要改动

| 阶段 | 已实现的工程能力 | 对应 PR / 主干合并提交 | 阶段状态 |
|---|---|---|---|
| P0：指令—运行一致性 | 未应用的 Agent 增量指令先生成/校验再运行；失败保留已接受配置；`source_request_id` 与严格 TaskSpec/SHA 绑定；可选参数增量合并 | [#89](https://github.com/clonetimer/SatAgentSimulation/pull/89) / `02c781ea` | 已合并 |
| P1：受控自动实验 | 结构化实验工具、参数与故障查询、预览、幂等 create/launch、总量上限、operation lease/owner、失联对账、run/queue/experiment 关联、结果查询 | [#90](https://github.com/clonetimer/SatAgentSimulation/pull/90) / `1ba609fa` | 已合并 |
| P2-A：确定性证据 | 生成 ExperimentEvidence，联结变体、Run Bundle、metrics、events、assertions、validation、完整性和 provenance；缺失/未封存 fail-closed | [#91](https://github.com/clonetimer/SatAgentSimulation/pull/91) / `ae93ba75` | 已合并 |
| P2-B：证据约束解释 | 默认 deterministic；可选已注册 Provider 的 model 解释；结构化 observations/comparisons/hypotheses/next_experiments/unresolved；run/metric/delta 与建议参数验证 | [#92](https://github.com/clonetimer/SatAgentSimulation/pull/92) / `0cfdf751` | 已合并 |
| P2-C：GoalRun 协调 | 冻结基础任务与参数白名单，跨实验累计 experiments/runs/execution_attempts/model_calls 预算，持久化实验与顺序决策，幂等重放与状态约束 | [#93](https://github.com/clonetimer/SatAgentSimulation/pull/93) / `43b0ebbb` | 已合并 |
| P3：经验适用性与路由 | capability/mode/effect/snapshot/TaskSpec version 适用性过滤先于 limit；短句 refinement 绑定 base TaskSpec；严格评测对象校验；可选同预算评测摘要驱动 L1/L2 校准 | [#94](https://github.com/clonetimer/SatAgentSimulation/pull/94) / `d51f1419` | 已合并 |
| 收敛增强 A | 记录修复前首次模型尝试；将首答、修复后成功、Run Bundle 与 native 执行、证据检查、token/latency 的评价分母分开；校验 run seal+manifest+artifact SHA | [#95](https://github.com/clonetimer/SatAgentSimulation/pull/95) / `5ea2664f` | 已合并 |
| 收敛增强 B | 经验检索 trace、计数、请求指纹、快照完整性、审计链、tenant/project 查询和完整性核验；标注“检索/注入不等于模型使用” | [#96](https://github.com/clonetimer/SatAgentSimulation/pull/96) / `4d35ab5c` | 已合并 |

## 3. 当前能力链路

用户请求 / 表单 / Canonical TaskSpec
→ 能力注册表与 TaskSpec/参数/物理边界校验
→ P0 来源和编辑应用一致性
→ P1 受控实验预览 + 幂等创建 + 带预算 launch
→ 原有 DurableExecutionQueue / 注册 Runner / Basilisk 调用链
→ Run Bundle（plan hash、provenance、validation、seal）
→ P2 ExperimentEvidence + deterministic/model 约束解释
→ P2 GoalRun 状态/预算/决策账本
→ P3 经验适用性检索 + 可选评测证据驱动模型 tier 校准。

**架构没有另建纯 Python 替代求解链**。Agent 与 GoalRun 不对模型支持能力、可执行 effect、物理因果或 Claim 等级作最终裁决，正式执行仍必须经过注册能力、执行计划和运行证据。模型提出的下一轮实验是 proposal，**不会自动获得执行许可**。

## 4. CI / 测试证据（按 PR 最新 head 核对）

下表只记录实际成功的适用检查，不将 `skipped` 当作 PASS，也不将交叠用例数量合并为独立测试总量。

| 对象 | 最新 head 适用检查 | GitHub Actions 证据 |
|---|---|---|
| P0 / #89 | Agent 输入一致性、实际 Chromium、单次真实 Basilisk、持续原生 Basilisk、持续界面、无 Basilisk 单测、静态、治理均成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42045180370)、[有限原生](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42045180391)、[持续原生](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42045180601) |
| P1 / #90 | Agent、单次真实 Basilisk、治理成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42051782195)、[Basilisk](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42051782122)、[治理](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42051782125) |
| P2 证据 / #91 | Agent、单次真实 Basilisk 成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42052922520)、[Basilisk](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42052922515) |
| P2 解释 / #92 | Agent、单次真实 Basilisk 成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42054974002)、[Basilisk](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42054973799) |
| P2 GoalRun / #93 | Agent、单次真实 Basilisk 成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42057191089)、[Basilisk](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42057191345) |
| P3 / #94 | Agent（含经验与路由测试）、单次真实 Basilisk、治理成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42061596528)、[Basilisk](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42061596085)、[治理](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42061596089) |
| 评测收敛 / #95 | Agent、治理成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42136293174)、[治理](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42136293148) |
| 检索审计 / #96 | Agent、单次真实 Basilisk 成功 | [Agent](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42135532069)、[Basilisk](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42135531536) |

这些记录证明的是**对应提交、对应检查集合**通过。主干 squash 后的 SHA 不等同 PR head；以上链接保留了提交绑定的原始检查。不能据此宣称完整全仓 pytest、跨平台部署、真实 LLM 性能、完整原生物理覆盖或长期运行全部通过。

### 4.1 可复核的专项测试

```bash
node --check src/sat_sim/web/app.js
python -m pytest -q tests/test_agent_pending_input_vm.py tests/test_agent_experiment_tools.py tests/test_agent_evidence_analysis.py tests/test_agent_evidence_interpreter.py tests/test_agent_goal_runs.py tests/test_agent_goal_api.py tests/test_experience_lessons.py tests/test_model_router_calibration.py tests/test_agent_eval_attribution.py
```

实际 CI 的完整测试集、固定依赖、真实 Chromium 与真实 Basilisk 环境以以下工作流为准：

- `.github/workflows/agent-automation-p0.yml`：Agent 契约、浏览器、经验/路由/评测回归；
- 单次真实 Basilisk workflow（在对应 PR 的 Actions 中检查具体 workflow 与 run）；
- 持续原生 Basilisk / 单元 / 浏览器 / 静态及脚本依赖治理工作流（在 P0 提交上核验）。

## 5. 收尾验收矩阵

| 验收主题 | 状态 | 精确含义 |
|---|---|---|
| P0 原始输入→有效配置→运行绑定 | **PASS（专项）** | pending/stale/rejected 状态与 source hash 闭环通过；不代表抵御任意磁盘篡改 |
| P1 自动实验、幂等、上限、lease 与恢复 | **PASS（专项）** | 受控 create/launch、失联对账与去重；不等于跨进程恢复物理求解器状态 |
| P2 证据和模型解释 | **PASS（专项）** | evidence 关联、delta/字段校验、建议范围限制；结论是工程证据解释 |
| P2 跨轮预算、决策账本 | **PASS（专项）** | GoalRun 后端状态/预算/关联/幂等通过；前端 GoalRun 专门工作台不属于本次冻结范围 |
| P3 适用性、严格证据与路由 | **PASS（专项）** | 过滤次序、证据对象校验、坏校准回退、tier 选择契约通过 |
| 经验复用提升真实模型效果 | **NOT_EVALUATED** | 尚无独立固定案例集的真实模型同预算 A/B 数值结论；不能报成功率提升 |
| 路由校准的真实部署效益 | **NOT_EVALUATED** | 已有可选机制与测试夹具，仍需真实 Provider、有效评测制品、同预算比较 |
| 所有能力 Basilisk-native / 物理精度 | **NOT_QUALIFIED** | 现有能力混合官方 native、项目实现与代理；不能把全平台宣称为原生或硬件级 |
| 整体 Stable / 当前源码长期可靠性 | **NOT_QUALIFIED** | 需参考当前 release_manifest、严格资源、长时间同源实例和交付环境实际验证 |

## 6. 未完成事项、下一期边界与停止条件

以下事项**不是 P0～P3 代码合并阻塞项**，但必须保留为后续独立验收，不能在本阶段报告中写成已完成：

1. **真实模型配对评测**：预注册固定任务集；相同 Provider、模型版本、推理参数、token/时间/repair 预算；baseline vs lesson-enabled 各运行同一案例，记录首答通过率、最终任务、原生执行、证据准确率、token/latency 和失败分类。保留不利/失败案例；生成可复核的 Run Bundle 与评测 SHA。
2. **模型路由在线校准效益**：让校准文件来自真实配对评测；建立对照/回退阈值。非法或证据不足的校准继续 fail-safe。
3. **实际使用 UI**：GoalRun 原生工作台已通过独立 PR [#97](https://github.com/clonetimer/SatAgentSimulation/pull/97) 合入 main（merge commit `7bbde7e`）；它属于 P0～P3 冻结之后的后续功能，不改变本报告的范围和通过声明。
4. **原生覆盖、部署与长测**：按 Capability implementation_class 和真实 Run Bundle 逐条验收；保留 project-deterministic、mixed-native、proxy 的准确分类。当前全平台 Stable、Windows/Ubuntu 各环境与 24h 同源码可靠性资格需独立证明。

**收尾决定**：冻结 P0～P3 的已合并功能范围，不继续以同一阶段名义加入新 Agent 能力；除回归失败、安全问题或真实使用阻断外，功能扩张放入后续独立 PR。PR #97 已作为后续功能独立合入；真实模型效果评测仍属于后续工作。

## 7. 追溯与使用入口

- 阶段报告：本文件。
- 第一阶段详细实现：[`docs/agent_automation_p0.md`](agent_automation_p0.md)。
- 工作台与可执行代码：[`src/sat_sim/web/`](../src/sat_sim/web)、[`src/sat_sim/api.py`](../src/sat_sim/api.py)。
- Agent 工具和编排：[`src/sat_sim/agent_tools.py`](../src/sat_sim/agent_tools.py)、[`src/sat_sim/goal_runs.py`](../src/sat_sim/goal_runs.py)。
- 结果证据与解释：[`src/sat_sim/evidence_analysis.py`](../src/sat_sim/evidence_analysis.py)、[`src/sat_sim/evidence_interpreter.py`](../src/sat_sim/evidence_interpreter.py)。
- 经验/路由与评测：[`src/sat_sim/experience_lessons.py`](../src/sat_sim/experience_lessons.py)、[`src/sat_sim/model_router.py`](../src/sat_sim/model_router.py)、[`src/sat_sim/experience_longitudinal_eval.py`](../src/sat_sim/experience_longitudinal_eval.py)、[`src/sat_sim/agent_eval_attribution.py`](../src/sat_sim/agent_eval_attribution.py)。
- 产品总体资格仍以 [`src/sat_sim/release_manifest.json`](../src/sat_sim/release_manifest.json) 和 [`reports/current_requirements_status.md`](../reports/current_requirements_status.md) 为准，不得用本专项报告覆盖其未完成状态。

本报告的“通过”只表示**专项功能实现与选定代码回归**；模型性能、物理可信度、发布资格和自动化权限边界保持各自独立。
