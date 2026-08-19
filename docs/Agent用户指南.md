# 卫星仿真 Agent 用户指南

## 1. 适用范围

本指南适用于当前正式交付包。当前版本已完成静态正确性、重复定义、异常诊断和 effect-QoI 证据收口，并固化发布身份、重复评测、文档和制品。系统通过受约束的 TaskSpec 流程创建、规划、运行和验证卫星仿真，不允许大模型自由生成并执行任意 Python 或直接构造 Basilisk 私有对象。

正式链路：

```text
自然语言 / 表单 / YAML / Patch
→ CanonicalTaskSpec 1.0
→ ResolvedSpec
→ Validated ExecutionPlan
→ Plan Hash 提交
→ 注册 Runner
→ Run Bundle
→ ValidationOutcome
→ ClaimReport
```

## 2. 安装

### 2.1 完整源码包

```bash
python -m pip install -r requirements.txt
python -m pip install -e .[api,dev]
```

### 2.2 Wheel

```bash
python -m pip install "satellite_simulation_platform-0.5.6.6-py3-none-any.whl[api]"
```

Wheel 保持轻量，不包含约 120 MB 的完整 SPICE 大内核。严格高保真环境场景应使用完整源码包，或通过环境变量指定外部资源目录。

## 3. 首次环境检查

```bash
sat-agent version
sat-agent doctor --require-api
```

严格检查 WMM/SPICE：

```bash
sat-agent doctor --require-api --strict-assets
```

环境资源搜索顺序：

1. TaskSpec 或 API 显式路径；
2. `SAT_SIM_WMM_PATH` / `SAT_SIM_SPICE_PATH`；
3. `SAT_SIM_ASSET_ROOT/wmm` 与 `SAT_SIM_ASSET_ROOT/spice`；
4. 完整源码包中的 `third_party/`；
5. 开发环境 `/mnt/data` 兼容路径。

## 4. 创建任务

### 4.1 自然语言

```bash
sat-agent parse "创建电池部件正常仿真，运行300秒"
```

模型只负责语义抽取。能力是否存在、owner、QoI、参数和 Claim 上限由注册表和确定性门禁决定。

### 4.2 表单或 YAML

```bash
sat-agent validate examples/component_nominal.yaml
sat-agent plan examples/whole_spacecraft_combined_nominal.yaml
```

### 4.3 导出复现脚本

```bash
sat-agent export-script examples/component_nominal.yaml \
  --output generated/component_nominal.py
```

导出脚本只调用已登记公开 Adapter，不是模型自由代码。

## 5. 运行与结果

```bash
sat-agent run examples/component_nominal.yaml --output-root runs
```

运行过程采用两阶段提交：

```text
prepare_run → 固化 Plan Hash → execute exact Plan Hash
```

查看运行：

```bash
sat-agent inspect runs/<run_id>
sat-agent report runs/<run_id>
```

Run Bundle 保存 TaskSpec、ResolvedSpec、ExecutionPlan、运行尝试、数据、验证、Claim 和 SHA-256 清单。封存后的旧 Run 不应修改；修订任务应创建新 Run，并可通过 `supersedes_run_id` 建立关系。

## 6. 正确理解状态

执行状态与任务验证是两套维度：

| 执行状态 | 验证结果 | 含义 |
|---|---|---|
| `SUCCEEDED` | `PASS` | 仿真运行成功且任务断言满足 |
| `SUCCEEDED` | `FAIL` | 仿真运行成功，但任务指标明确失败 |
| `SUCCEEDED` | `INCONCLUSIVE` | 仿真完成，但证据不足 |
| `FAILED/CANCELLED` | `NOT_EVALUATED` | 无法评价任务目标 |

故障与退化还区分：

```text
Delivery：事件是否注册并进入有效时间窗
Effect：是否存在足够的因果 QoI 证据
```

事件投递成功不等于物理效果已证明。既有工程版本 中 13 项 source-native 注册实现已有直接 QoI 证据；太阳翼展开故障仍为显式项目代理，电池自放电率增加仍为交付范围外。

## 7. 本地 API

```bash
sat-agent serve --host 127.0.0.1 --port 8000
```

主要接口：

- `GET /health`、`GET /health/details`、`GET /release`；
- `POST /tasks/parse|validate|resolve|plan`；
- `POST /runs`；
- `POST /runs/{id}/execute`；
- `GET /runs/{id}/report|artifacts`。

当前 API 为本地同步接口，不包含生产鉴权、TLS、多租户或分布式任务队列。

## 8. 正式评测与发布检查

```bash
sat-agent eval --repeat-count 5 --execute-marked
sat-agent release-check \
  --golden-report reports/agent_golden/golden_eval_report.json \
  --source-root . \
  --strict-assets
```

不得通过减少案例、降低重复次数、删除拒绝样本或放宽断言来提高指标。

## 9. 常见错误

- `REQUEST_LEVEL_CONFLICT`：用户明确层级与所选能力不一致；
- `EFFECT_OWNER_OUTSIDE_DEPENDENCY_GRAPH`：effect owner 不在执行依赖闭包；
- `REQUIRED_QOI_MISSING`：请求结果无法由能力提供；
- `CLAIM_EXCEEDS_PARAMETER_PROFILE`：参数证据不足以支持声明；
- `PLAN_HASH_MISMATCH`：执行阶段提交的计划与准备阶段不一致；
- `VALIDATION_INCONCLUSIVE`：缺少足够物理证据，不应改写为 PASS。
