# 全场景仿真、自然语言闭环与经验积累规格

日期：2026-07-29

## 1. 目标

本轮只完成三个产品目标：

1. 当前工作台定义的全部部件、分系统和整星对象，支持正常运行及经治理的常见故障、退化仿真。
2. 用户输入卫星仿真自然语言描述后，平台生成 Canonical TaskSpec 和可复现仿真脚本，并实际运行、验证物理约束和输出可信边界。
3. 建立参考 `Self-Improving_Agents_Survey.pdf` 的经验基础设施，使部署轨迹能够被捕获、验证、持久化、检索和受控复用。

## 2. 非目标

- 不支持任意、未建模或没有物理合同的故障。
- 不让 LLM 生成或执行任意 Python。
- 不让经验修改 Capability Registry、物理参数合同、验证阈值或 Claim Guard。
- 不在本轮自动微调模型权重。
- 不把工程仿真声明为真实硬件、硬件标定或飞行验证。

## 3. 范围

权威对象范围来自 `workbench_presentation_catalog()`：

- 24 个部件；
- 6 个分系统；
- 1 个整星；
- 轨道环境作为跨对象公共环境能力单独验收，不计入 31 个对象分母。

“全部支持”以对象为单位，不以 Capability 文件数为单位。兼容、内部和重复 Capability 不能提高产品覆盖率。

## 4. 场景合同

每个对象必须有 `scenario_baseline`：

```text
object_id
  nominal:
    capability_id
    parameter_profile
    required_outputs
    physical_checks
  faults[]:
    scenario_id
    owner
    source/FMEA rationale
    parameters and ranges
    onset/duration
    injection delivery evidence
    direct effect evidence
    physical checks
  degradations[]:
    与 faults 相同的治理字段
```

常见场景由现有 FMEA、工程模型、已注册 effect 和专家审查共同冻结。每个对象至少有一个正常场景、一个代表性常见故障和一个代表性退化；确实无合理退化机制时，必须由领域审查给出 `N/A`，不能由开发者自行跳过。

## 5. 全模式可执行定义

一个场景只有同时满足以下条件才计为支持：

1. 主 Capability 为 active，且工作台、Agent、CLI 使用同一 ID。
2. `runtime_run=true`、`script_export=true`。
3. TaskSpec 严格校验和 ExecutionPlan 编译通过。
4. 脚本只调用注册执行入口，不嵌入新物理实现。
5. Run Bundle 状态 `SUCCEEDED` 且密封完整性通过。
6. ValidationOutcome 为 PASS。
7. 故障/退化同时具有注入交付和直接效果证据。
8. 物理检查至少覆盖适用的有限性、范围、守恒、单调性、因果方向或前后等价性。
9. ClaimReport 不超出模型可信等级。

## 6. 自然语言端到端协议

```text
Natural Language
  -> live catalog grounding
  -> model route and invocation evidence
  -> candidate TaskSpec
  -> deterministic repair
  -> strict validation
  -> ExecutionPlan
  -> deterministic wrapper script
  -> registered execution
  -> sealed Run Bundle
  -> physical ValidationOutcome
  -> experience capture
```

真实模型验收使用外部 vLLM `http://127.0.0.1:8000/v1`。模板后端只用于离线单元测试，不得计入真实模型通过率。

自然语言矩阵必须覆盖：

- 31 个对象的正常请求；
- 场景基线中的每个批准故障和退化；
- 参数、时长、采样、输出和耦合修改；
- 歧义、缺参数、越权、未知能力、未知效果、过度声明和提示注入；
- 中文、英文及关键术语别名。

每个案例记录模型调用哈希、原始响应哈希、修复日志、最终 TaskSpec、脚本哈希、Run Bundle、ValidationOutcome 和回退状态。

当前工程证据基线：中文、英文和关键术语别名真实 vLLM 接受矩阵已完成 31 对象 × 正常/故障/退化三模式 × 3 变体，共 279/279 PASS；每例均完成 TaskSpec、确定性脚本、Run Bundle 和 ValidationOutcome 闭环，模板/规则回退为 0。歧义、越权、未知对象/效果、过度声明和提示注入结构化拒绝矩阵 10/10 PASS，false accept、脚本生成和模型调用均为 0；安全边界在模型调用前 fail-closed。

## 7. 经验基础设施

### 7.1 Experience Record

新增版本化 Schema，最少包含：

- `experience_id`、时间窗口和租户/项目作用域；
- 请求、DAG、TaskSpec、Plan、脚本和 Run Bundle 哈希；
- 模型/模板身份、工具调用、重试和错误；
- ValidationOutcome、用户/专家反馈和归因；
- Capability、模式、故障/退化、参数范围和环境标签；
- 信任等级：`raw`、`verified`、`approved`、`revoked`；
- 安全标签、敏感字段摘要和完整性签名；
- 来源经验及派生关系。

### 7.2 Experience Store

采用 SQLite 元数据加内容寻址制品：

- 原始轨迹不可变；
- 编译经验与原始证据分离；
- 支持按 Capability、场景、错误码、参数区间和结果检索；
- 每次读写均有审计事件；
- 支持备份、恢复、Schema 迁移和撤销。

当前实现基线（2026-07-29）：

- `sat-sim.experience-record.v1` Schema、不可变模型和 `sat-sim` Store Schema v2 已落地；
- 密封 Run Bundle 必须先通过现有 `verify_run_bundle`，再捕获 TaskSpec、ExecutionPlan、ValidationOutcome、ClaimReport、运行记录和包清单；
- 请求及 JSON 制品递归脱敏，原敏感值只保留不可逆摘要；
- 检索支持 Capability、模式、effect、错误码、ValidationOutcome 和信任等级，并强制租户/项目作用域；
- 读写审计采用并发串行哈希链；并发读写、篡改、撤销、保留清理、v1→v2 迁移和备份恢复专项测试 18/18 PASS；
- CLI、Python API 和 FastAPI 产品接口已完成；阶段 5 `G4_EXPERIENCE_RESERVOIR` 关闭。

### 7.3 Experience Compiler

编译窗口关闭后执行：

```text
capture
  -> redact
  -> deduplicate
  -> attribute
  -> verify
  -> conflict check
  -> candidate lesson
  -> held-out evaluation
  -> named approval
  -> approved reusable experience
```

首轮只允许形成三类外部经验：

- 已验证的参数/输出解释；
- 已验证的失败恢复建议；
- 已验证的相似 TaskSpec/DAG 示例。

不生成可执行代码技能，不自动修改模型参数。

### 7.4 Retrieval and Reuse

检索结果是 advisory context：

- Registry 和 Validator 始终权威；
- 只检索同作用域、未撤销且版本兼容的经验；
- 默认仅使用 `approved`；
- 检索内容进入模型提示前带来源、信任等级和哈希；
- 经验导致的 TaskSpec 差异必须可解释；
- 无经验或经验冲突时回退到当前确定性主线。

当前受控复用基线（2026-07-29）：

- 候选 lesson 仅由未撤销、密封且完整性通过的 Experience Record 编译，不复制原始提示，不生成可执行代码；
- held-out 评测是审批前置门，编译者、评测者和审批者职责分离，批准需要两名不同具名审批者；
- 产品 API 对 review、revoke、snapshot、reuse 和 rollback 强制 `admin`，读取为 viewer，编译/评测为 operator；
- Agent 只读取活动快照内 approved、未撤销、同作用域、TaskSpec 版本兼容的 lesson，并记录 lesson ID、哈希和影响归因；
- 一键禁用、指定快照回滚、撤销后立即停止命中已实现；
- 专项测试 28/28、正式受控经验验收 18/18 PASS，阶段 6 `G5_CONTROLLED_REUSE` 关闭。

## 8. 纵向评测

每次经验晋级必须进行 A/B：

- A：无经验基线；
- B：批准经验检索；
- 固定 held-out 案例、模型、随机种子和预算。

测量：

- TaskSpec 有效率、首次通过率和实际运行通过率；
- 物理 ValidationOutcome 通过率；
- 平均重试、Token、延迟和执行成本；
- 对旧案例的后向保持；
- 最坏类别回归；
- 经验命中归因和无关经验干扰；
- 安全拒绝率和过度声明率。

晋级要求 B 在预先冻结的主指标上改善，且任何安全、物理真实性、后向保持指标不得下降。

当前纵向与四维评测基线（2026-07-29）：

- 真实 vLLM 使用温度 0、种子 0、最大 2048 Token 和 TaskSpec JSON Schema；6/6 配对案例均节省 Token，精确符号检验 `p=0.03125`，中位总 Token 节省 52，中位延迟节省 1229.35 ms；
- A/B 两组实际物理运行通过率均为 100%，模型证据完整，模板/规则回退为 0，安全、物理和后向保持门无下降；
- 容量矩阵 47/47 PASS，实测边界为并行 4、批量 16、仿真时长 3600 秒、最小采样周期 0.1 秒、单次 8 个治理事件；
- 1k/10k/100k 经验检索基准 PASS；投毒、提示/反馈注入、越权、哈希篡改和撤销/回滚攻击矩阵均 fail-closed；
- 固定经验快照与种子的模型语义结果可复现，31/31 对象确定性矩阵、vLLM 中断恢复和 Worker 崩溃重领后真实执行均 PASS；
- 聚合证据见 `reports/reliability_acceptance/report.json`，8/8 检查通过，`G6_LONGITUDINAL_EVAL` 关闭。

## 9. 四维验收要求

### 功能

- 31/31 对象都有主能力和场景基线。
- 全部批准场景可从表单和自然语言执行。
- 每次运行都可生成 TaskSpec、脚本、证据和报告。
- 经验捕获、检索、审批、撤销端到端可用。

### 性能

- 场景门使用分层矩阵，PR 运行代表集，夜间运行全量集。
- vLLM 记录 p50/p95 延迟、Token 和重试。
- 经验检索在 10k 记录下 p95 不高于冻结预算。
- 批量仿真不得突破现有单机容量和显存预算。

### 安全

- 任意经验不得引入未知 Capability、effect、参数或外部代码。
- 经验投毒、提示注入、反馈操纵、越权晋级和哈希篡改均 fail-closed。
- 批准、撤销和发布需要具名身份。
- 秘密和原始敏感文本不得进入可检索正文。

### 可靠性

- 经验更新可回滚，旧版本任务可复现。
- Store 备份恢复和迁移通过。
- 相同输入、经验快照和种子可重现同一语义结果。
- Worker 中断、vLLM 超时和无经验条件下均有明确恢复或结构化失败。

## 10. 发布条件

只有 `checklist.md` 的 G0-G7 全部 PASS 才可宣称三个目标完成。在此之前，当前 `INTERNAL_ENGINEERING_BASELINE_READY` 只代表上一轮已批准范围，不代表本规格的全覆盖目标。
