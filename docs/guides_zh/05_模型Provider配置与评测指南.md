# 模型 Provider 配置与评测指南

## 1. Provider 定位

Provider 输出是**非可信 TaskSpec 草稿**。下列层级不由模型决定：

- Capability 是否存在；
- 参数是否允许；
- 故障和退化是否受支持；
- TaskSpec 是否有效；
- ExecutionPlan 是否可执行；
- 运行结果是否通过。

## 2. 内置 Provider

- `local-template`：确定性离线基线；
- `local-command`：本地命令包装；
- `local-vllm`：本地 OpenAI-compatible 接口；
- `remote-deepseek`；
- `remote-openai`；
- `remote-openai-compatible`。

## 3. 配置文件

复制示例：

```bash
cp configs/model_providers/provider.example.json /etc/sat-sim/model_providers.json
export SAT_SIM_MODEL_PROVIDERS_FILE=/etc/sat-sim/model_providers.json
```

配置字段重点：

| 字段 | 含义 |
|---|---|
| `provider_id` | 唯一标识 |
| `backend` | vLLM、OpenAI-compatible 等后端类型 |
| `location` | local 或 remote |
| `model` | 服务端实际模型名 |
| `base_url` | OpenAI-compatible API 根地址 |
| `api_key_env` | 密钥环境变量名，不是密钥值 |
| `priority` | 自动路由优先级 |
| `enabled` | 是否参与路由 |

## 4. Readiness 检查

```bash
python -m sat_sim.cli model-providers
python -m sat_sim.cli model-providers --live-probe
```

Readiness 不等同于任务质量评分。托管模型未配置密钥时必须显示未就绪。

## 5. 正式评测

本地模板基线：

```bash
python -m sat_sim.cli provider-eval \
  --provider-id local-template \
  --provider-kind template \
  --cases evals/provider_eval_cases.json \
  --output-dir reports/provider_eval/local-template
```

DeepSeek 示例：

```bash
export DEEPSEEK_API_KEY='由密钥系统注入'
python -m sat_sim.cli provider-eval \
  --provider-id remote-deepseek \
  --provider-kind deepseek \
  --cases evals/provider_eval_cases.json \
  --output-dir reports/provider_eval/remote-deepseek
```

## 6. 评测诚信规则

正式报告必须记录：

- 测试用例文件 SHA-256；
- Provider ID、类型和模型身份；
- 非敏感配置指纹；
- 是否实际调用目标 Provider；
- 是否发生 fallback；
- 每条用例的原始状态和校验结果。

以下情况 `pass_rate` 必须为空：

- API Key 缺失；
- Provider 不可达；
- 实际调用了其他 Provider；
- 发生模板回退；
- Provider 身份不能确认。

模板基线成绩不得标记为真实 DeepSeek、Qwen、OpenAI 或 vLLM 成绩。
