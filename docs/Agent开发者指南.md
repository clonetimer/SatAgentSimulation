# 卫星仿真 Agent 开发者指南

## 1. 架构原则

1. TaskSpec 是用户意图的唯一事实来源；
2. Capability Registry 是可执行能力的唯一事实来源；
3. Parameter Registry 与 Validation Evidence 决定 Claim 上限；
4. LLM、RAG 和示例只能提供候选语义，不得覆盖注册表；
5. 正式执行走公开 Adapter/Runner；脚本仅用于复现；
6. mission FAIL、证据不足和 unsupported 请求禁止自动“修复”为 PASS。

## 2. 主要模块

| 模块 | 职责 |
|---|---|
| `task_models.py` | Canonical TaskSpec Pydantic 模型与旧版迁移 |
| `capability_registry.py` | 能力与生命周期注册表 |
| `operator_contract.py` | owner/effect/observability/执行策略规范化 |
| `unified_agent.py` | 自然语言、表单、TaskSpec 和 Patch 统一入口 |
| `execution_planner.py` | ResolvedSpec、依赖图、绑定和受限 DAG |
| `run_bundle.py` | Plan/Execute、Run/Attempt、封存与完整性 |
| `validation_outcome.py` | 四态验证、注入证据和 ClaimReport |
| `golden_eval.py` | 140 案例和重复一致性评测 |
| `release_closure.py` | 既有工程版本 环境诊断、代表运行和 12 项终止条件 |

## 3. 新增能力步骤

1. 在 `src/sat_sim/capabilities/` 新增 YAML；
2. 指定稳定 `capability_id`、level、owner、公开 Adapter；
3. 声明参数、依赖、effect 和输出；
4. effect–QoI 只有具备运行证据时才能标为 verified；
5. 实现公开 Adapter 的 `validate/run/generate_python/output_schema`；
6. 添加正常、故障、退化、拒绝和边界测试；
7. 更新 Golden Set 生成器，不直接手改正式清单；
8. 执行 Operator Registry 审计和完整发布检查。

## 4. 版本和兼容性

- Canonical TaskSpec 当前为 `1.0.0`；
- 旧 `0.1.0` 只能通过确定性迁移器进入主链；
- deprecated capability 必须提供明确 replacement；
- 不允许因兼容历史文件而重新暴露私有或 legacy 执行入口。

## 5. 测试命令

```bash
PYTHONPATH=src:. python -m compileall -q src scripts
PYTHONPATH=src:. pytest -q
PYTHONPATH=src:. python scripts/run_v27_cli_api_smoke.py
PYTHONPATH=src:. python scripts/run_v28_release_closure.py \
  --golden-report reports/agent_golden/golden_eval_report.json \
  --source-root . --strict-assets
```

发布前必须运行当前静态与证据门禁，构建 wheel/sdist，并在项目目录之外只安装 wheel 进行隔离验证。正式重复评测使用 `reports/agent_golden/golden_eval_report.json`。

## 6. Schema 生成

既有工程版本 发布 Schema 来自 Pydantic 源：

```python
from sat_sim.release_closure import environment_doctor_schema, release_closure_report_schema
```

提交前应确认生成结果与 `src/sat_sim/schemas/` 完全一致。

## 7. 修复边界

允许：字段迁移、登记的 capability replacement、非语义输出路径规范化。

禁止：删除 QoI、放宽阈值、改变事件语义、提升参数证据、静默切换 proxy、用输出后处理伪造物理效果。
