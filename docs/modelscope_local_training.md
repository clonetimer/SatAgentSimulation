# ModelScope 本地微调与脚本生成

本项目已经实际训练 ModelScope 来源的 `Qwen/Qwen2.5-Coder-1.5B-Instruct`，通过独立本地 HTTP 服务提供模型推理，再由平台将模型输出的 TaskSpec 校验、编译为仿真脚本并执行。模型原始回答和实际调用来源保留；模板回答不作为模型生成成功。

v5 已在冻结 App4cce／Core59 的当前生产路径实际通过热环境、4 W/K 导热参数、电池容量损失、三项创建／修改和85项模型网页链路；851项独立总复核、10份原生封存现查及45张原图实际看图复核通过。原文、实际脚本、CSV和封存均保留独立证据，此前页签／文案及小屏取景问题已修复并复验。最新判定见[需求验收状态](../reports/current_requirements_status.md)。原始冻结输入的0/69失败分数、训练来源和历史候选记录继续保留；不外推任意自然语言或无约束模型调用的准确率。项目整体仍等待已启动的实际24小时耐久及终态复核。

## 当前本地服务与工作台

服务使用 `http://127.0.0.1:8031/v1`，模型名为 `Qwen2.5-Coder-1.5B-SatAgent-v5-guided-graph-v8`。当前复验条件为 `json_schema`、300 秒超时、4096 输出 token、语义校验及错误拒绝；API Key 对该本机服务留空即可。配置文件为 `reports/local_requirements_completion/modelscope-v5-graph-v8-local-service/providers.json`；新解码器启动与同一权重的健康记录为 `reports/local_requirements_completion/empty-array-efficient-v5-service/state.json`，现时存活与验收阶段以需求报告引用的进程观察和资格流程为准。

在项目根目录用已有 uv 环境启动工作台：

```powershell
$env:SAT_SIM_INTERACTIVE_ENABLED='1'
$env:SAT_SIM_SUPERVISED_NATIVE_RUNTIME='1'
$env:SAT_SIM_MODEL_PROVIDERS_FILE=(Resolve-Path 'reports/local_requirements_completion/modelscope-v5-graph-v8-local-service/providers.json').Path
uv --cache-dir .uv-cache run --no-sync python -m sat_sim.agent_cli serve --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`，在“模型服务”中选择 ModelScope 微调模型，自动发现上述模型，再“保存并完整测试”。自然语言需求先生成 TaskSpec，经校验后导出脚本；请检查参数、事件、时间及输出后运行。模型回答与校验失败记录均保留。

后文保留环境、训练和候选评测的复现说明；历史“尚未接受”等措辞仅适用于其对应修订，不代表当前服务状态。

## 两个独立环境

仿真与网页环境使用仓库根目录的 `.venv`：

```powershell
python scripts/setup_uv.py
```

GPU 训练和模型服务使用 `.venv-sft`。在新工作区创建该环境：

```powershell
$env:UV_CACHE_DIR = Join-Path (Get-Location) '.uv-cache'
uv venv --python 3.12 .venv-sft
uv pip install --python .venv-sft/Scripts/python.exe --find-links third_party/wheels -r requirements-sft-lock.txt
uv pip check --python .venv-sft/Scripts/python.exe
```

已有本轮训练环境时直接使用，不必重新创建。离线安装还需要完整依赖缓存或 wheel 集合，届时可给安装命令加 `--offline`；仅有源码不保证可离线安装。当前固定 GPU 依赖包括 torch `2.11.0+cu128`、transformers `4.57.6`、peft `0.18.1`、accelerate `1.12.0`、safetensors `0.7.0`、modelscope `1.40.2`，已从实际环境元数据核对。主应用的 OSV 审计结论不覆盖这个独立 GPU 依赖集合。

此前独立核对了 GPU 环境的全部 49 个锁定包。结构化解码增加 `lm-format-enforcer==0.11.3` 与 `interegular==0.3.3` 后，当前锁定包共 51 个，`uv pip check` 无冲突。uv 创建的环境可以没有 `pip` 模块，应使用上述 `uv pip check`；依赖相容不等于漏洞审计或模型能力通过。

本轮实际使用 NVIDIA GeForce RTX 5060 Laptop GPU、CUDA 12.8 的训练记录不等于其他显卡或平台的兼容性保证。训练和服务不应与正式性能、耐久测量并行竞争资源。

## 基座来源和数据

当前基座来源记录为 `.models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json`，包含逐文件哈希。首次获取时须显式指定本轮模型，脚本默认模型是另一个较小基座：

```powershell
.venv-sft/Scripts/python.exe scripts/fetch_sft_model.py --model-id Qwen/Qwen2.5-Coder-1.5B-Instruct --cache-dir .models
```

已有冻结来源记录时不要重新获取并覆盖它；复现另一份快照应使用新缓存目录及其新来源记录。下载脚本排除远程 Python 和 pickle 权重，训练及服务使用 `trust_remote_code=False`。远端分支可能变化，真正的本地复现身份以来源记录中的文件哈希为准。

当前冻结数据目录为 `data/training/taskspec_lora_v5_current_prompt_event_aligned`：训练 588 条、验证 69 条，使用当前生产提示和明确的事件幅度。两集合的记录 ID、原始请求和语义场景族无重叠，文件哈希与 manifest 一致。它们是注册表合成合同样本，不是实际在轨遥测。原始 v3 失败和修订前数据均保留。

新旧验证集保留相同的 69 个场景与时长；45 条业务请求文本相同，另 24 条补写默认故障幅度 `1.0`，生产提示包装也更新了。因此当前数据集成绩不等于原始冻结输入的同条件成绩。独立入口 `reports/local_requirements_completion/evaluate_v5_original_frozen_inputs.py` 已实际重放归档的 69 份完整输入载荷，仅改变模型名，不替换响应、标签或运行检查；结果为 **0/69，整批 FAIL**。此前“待重放”仅为历史准备状态，该失败分数不由当前产品路径通过记录覆盖。

## 训练复现

在仓库根目录运行，使用全新输出目录保留原始 v5 制品：

```powershell
.venv-sft/Scripts/python.exe scripts/train_modelscope_lora.py --dataset-dir data/training/taskspec_lora_v5_current_prompt_event_aligned --base-manifest .models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json --output-dir training_runs/qwen25_v5_reproduction --epochs 3 --max-length 12288 --loss-chunk-tokens 128 --attention-backend cudnn --rank 16 --gradient-accumulation 8 --learning-rate 0.0002 --seed 20261025
```

原始 v5 实际完成 222 个优化步、3 个 epoch，耗时约 14.6 小时；最长数据 11045 token，无截断。训练损失下降或验证损失很低都不能替代脚本生成正确率评测。种子、数据和软件身份有助于复现，不保证不同硬件逐 bit 相同。

原始训练制品位于 `training_runs/qwen25_coder15_taskspec_lora_v5_current_prompt_event_aligned`。`training_provenance.json` 的 `TRAINED_NOT_TASKSPEC_ACCEPTED` 表示训练结束但任务能力未接受。

## 独立服务与评测

正式性能队列完成后，候选评测器会释放已核验身份的旧模型服务、在 8032 启动 v5，完成后恢复 v3，避免同一 GPU 同时驻留两个服务。不要在该自动队列运行时重复启动候选。

独立评测时，服务命令如下，适配器路径和模型名必须匹配要测的制品：

```powershell
.venv-sft/Scripts/python.exe scripts/serve_modelscope_model.py --base-manifest .models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json --adapter training_runs/qwen25_coder15_taskspec_lora_v5_current_prompt_event_aligned/adapter --model-name Qwen2.5-Coder-1.5B-SatAgent-v5 --max-context 16384 --port 8032
```

服务仅绑定本机，提供 `/health`、`/v1/models`、`/v1/chat/completions`。健康响应需核对模型名、实际 adapter、训练来源哈希及数据 manifest 哈希；只验证端口可连接不够。

本地服务对 `json_object` 和 `json_schema` 请求使用 LM Format Enforcer 的逐 token 约束，并在生成后再次校验完整 JSON/Schema；普通文本请求保留原始解码方式。平台根据已选择能力的注册表生成参数名和类型约束，包含数组、向量与标量/数组联合类型，不向解码器提供期望答案。模型仍需自行生成数值、事件和任务内容；结构合法不代表用户语义正确，事件幅度、参数保留和物理执行检查均继续生效。响应中的 `structured_generation` 记录方法及 Schema 哈希，失败的原始输出保留。

在另一个终端，使用主环境执行完整 69 条注册表增强验证：

```powershell
.venv/Scripts/python.exe scripts/evaluate_modelscope_taskspec.py --model Qwen2.5-Coder-1.5B-SatAgent-v5 --base-url http://127.0.0.1:8032 --dataset-dir data/training/taskspec_lora_v5_current_prompt_event_aligned --all-validation --registry-constraints --output-dir reports/v5_reproduction_all69
```

去掉 `--all-validation --registry-constraints`、改用新输出目录，可执行各场景族原始提示检查；使用 `--product-chain --max-repair-attempts 1`、再改用新目录，执行生产创建与修改链。默认的六条族代表检查不能当成完整 69 条结果。还需要独立热环境生产脚本和实际模型网页流程，自动队列已包含前者。

旧 v3 为 68/69 通过、整批 FAIL：电池容量损失 0.3 被模型生成为 1.0，门禁拒绝执行。新模型不得覆盖这个原始结果；任何格式修复、确定性字段绑定与模型原始准确率都应分别披露。

## 网页使用

候选结果审查通过并选择服务后，在“模型服务”设置选择 ModelScope，填写服务的 `/v1` 地址，发现模型、保存并运行完整探测。当前网页默认请求合同为超时 300 秒、最多输出 4096 token、`json_schema`。成功后用自然语言创建或修改任务，检查参数与事件，再生成和下载 Python。

网页验收必须实际执行下载脚本，并验证原生遥测、结果判据及封存制品；仅看到代码或绿色探测提示不足以判定完整功能通过。独立入口 `reports/local_requirements_completion/run_actual_modelscope_browser.py` 已实际执行；后续可视区域增强入口 `run_actual_modelscope_browser_visible_r11.py` 还要求代码／结果目标实际入镜，并补拍真实模型结果的曲线与图例。各次结果和源码版本分别记录。

<!-- state: context_of_use=generic_LEO_small_satellite_engineering; channel_mode=markdown; audit_mode=workflow_reproduction; phase=production_qualification_PASS_actual24h_RUNNING; evidence=real_v5_product_stages_PASS_browser85_images45_independent851PASS; completion=unproven -->
## 数值约束与纠错的补充说明（2026-10-30）

结构化 JSON 生成不等于参数满足物理范围。当前服务在解码阶段增加标量数值边界门控，并在生成完成后继续执行完整 JSON Schema 校验；客户端对可解析的 Schema 拒绝响应最多请求模型纠错一次，保持相同约束，分别保存 `model_call_rejected.json`、`raw_model_output_rejected.txt` 与最终响应。不会通过钳位参数、放宽范围或替换回答获得通过结果。

该修订已完成上述真实模型、原生脚本执行和完整应用复验；原始热环境失败、纠错仍失败、中文字符表传播缺陷和主动中止的部分回归均保留。当前验收状态以[需求报告](../reports/current_requirements_status.md)为准；协议测试、产品链路实测与原始模型准确率分别记录，整体24小时资格仍待完成。
