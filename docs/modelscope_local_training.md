# ModelScope 本地微调与脚本生成

本项目已经实际训练 ModelScope 来源的 `Qwen/Qwen2.5-Coder-1.5B-Instruct`，通过独立本地 HTTP 服务提供模型推理，再由平台将模型输出的 TaskSpec 校验、编译为仿真脚本并执行。模型原始回答和实际调用来源保留；模板回答不作为模型生成成功。

**其他机器直接使用已训练模型：**优先按[模型下载与部署](model_distribution.md)执行 `python scripts/model_release.py install`，再在锁定GPU环境执行 `scripts/model_release.py serve`。下面的原始 `training_runs/...` 路径用于开发机器和训练复现，源码检出不会附带该目录；自动安装器会生成新机器自己的路径，无需重新训练。

当前v5已完成15次真实首答的原始脚本执行、密封结果复核，以及另2次安装态真实创建/修改模型调用和3次原生运行。本次维护未改变AI实现，未重新训练或增加模型推理；新安装态服务发现与当前原生网页流程另行核验。原文、实际脚本、CSV、封存及历史阶段检查均保留。最新判定见[需求验收状态](../reports/current_requirements_status.md)和[最终验收报告](../reports/progress_checkpoint/maintenance-closure-2026-11-02/README.md)。旧69例的严格0/69、原回答诊断54/69及最终对象严格比较0/69继续保留；15例通过不是新的69/69，不外推任意自然语言准确率。24小时已按用户选择的原始证据复核方式验收，原自动NOT_QUALIFIED不改写。

## 当前本地服务与工作台

本次验收服务地址为 `http://127.0.0.1:8033/v1`，模型名为 `Qwen2.5-Coder-1.5B-SatAgent-v5-r47-explicit-literals`，使用既有v5适配器。服务上下文16384 token、协作式请求截止270秒、不接受排队请求；客户端超时300秒，输出token上限以实际请求配置为准。请求使用 `json_schema`、语义校验及错误拒绝。API Key 对该本机服务留空即可。验收时服务已运行，不必重复启动；历史8031/8032地址和graph-v8名称不作为当前连接配置。服务是否现时在线须实际检查，不保证后台进程永久存活。

在项目根目录用已有 uv 环境启动工作台：

```powershell
$env:SAT_SIM_INTERACTIVE_ENABLED='1'
$env:SAT_SIM_SUPERVISED_NATIVE_RUNTIME='1'
uv --cache-dir .uv-cache run --no-sync python -m sat_sim.agent_cli serve --host 127.0.0.1 --port 8000
```

打开 `http://127.0.0.1:8000`，在“模型服务”中配置上述服务地址，发现并选择模型，再“保存并完整测试”。源码检出不附带本地私有配置文件；无需Resolve-Path到历史报告目录。当前独立安装配置如实保留CONFIGURED_NOT_TESTED，服务发现成功不等于新一轮完整生成探测；先前真实创建/修改验收按其原版本记录。自然语言需求先生成TaskSpec，经校验后导出脚本；检查参数、事件、时间及输出后运行。模型回答与校验失败记录均保留。

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

## 训练前的数据合同检查

`scripts/train_modelscope_lora.py`现在在调用GPU训练函数前检查注册表合成数据的默认曲线合同与训练/验证ID分离。可先单独保存只读报告：

```powershell
.venv/Scripts/python.exe scripts/audit_registry_plot_contract.py --dataset-dir data/training/taskspec_lora_v5_current_prompt_event_aligned --output reports/modelscope_dataset_contract.json
```

输出必须是新文件。此检查只覆盖已知注册表提示格式、默认曲线约定及ID分离，不代表完整数据质量或模型准确率。当前v5的588训练/69验证记录共657条通过，原v3的576训练/69验证记录共645条均因该合同问题被拒绝；旧数据未修改。CLI控制流已实际检查：冲突数据在调用训练前退出，当前数据能进入训练函数；该检查使用记录器代替训练函数，没有重新训练GPU模型。

原始v5重放的严格0/69 FAIL仍保留。新增只读复核确认，这69条旧提示要求`default_trace_fields`用于曲线，规范化参考标签却为空列表；65份可解析回答在这一字段上遵循了提示，其中54份只在此字段与标签不同，11份还有求解器或幅度差异，4份不可解析。不能把0/69直接解释为全部物理参数错误，也不能据此声称已取得另一准确率或免除其余错误。当前对齐数据通过合同检查不等于任意提示泛化已经证明。

## 训练复现

在仓库根目录运行，使用全新输出目录保留原始 v5 制品：

```powershell
.venv-sft/Scripts/python.exe scripts/train_modelscope_lora.py --dataset-dir data/training/taskspec_lora_v5_current_prompt_event_aligned --base-manifest .models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json --output-dir training_runs/qwen25_v5_reproduction --epochs 3 --max-length 12288 --loss-chunk-tokens 128 --attention-backend cudnn --rank 16 --gradient-accumulation 8 --learning-rate 0.0002 --seed 20261025
```

原始 v5 实际完成 222 个优化步、3 个 epoch，耗时约 14.6 小时；最长数据 11045 token，无截断。训练损失下降或验证损失很低都不能替代脚本生成正确率评测。种子、数据和软件身份有助于复现，不保证不同硬件逐 bit 相同。

原始训练制品位于 `training_runs/qwen25_coder15_taskspec_lora_v5_current_prompt_event_aligned`。`training_provenance.json` 的 `TRAINED_NOT_TASKSPEC_ACCEPTED` 是训练结束时的历史状态；后续受约束产品链验收另有证据，不改写该原始记录，也不代表无约束生成已被接受。

## 独立服务与评测

历史候选队列曾在8032评测v5后恢复v3；该流程不是当前服务状态。当前使用上述8033服务，启动前先检查既有服务和GPU占用，避免重复驻留同一权重。

独立评测时，服务命令如下，适配器路径和模型名必须匹配要测的制品：

```powershell
.venv-sft/Scripts/python.exe scripts/serve_modelscope_model.py --base-manifest .models/Qwen--Qwen2.5-Coder-1.5B-Instruct-provenance.json --adapter training_runs/qwen25_coder15_taskspec_lora_v5_current_prompt_event_aligned/adapter --model-name Qwen2.5-Coder-1.5B-SatAgent-v5-r47-explicit-literals --max-context 16384 --request-deadline-s 270 --max-waiting-requests 0 --port 8033
```

服务仅绑定本机，提供 `/health`、`/v1/models`、`/v1/chat/completions`。健康响应需核对模型名、实际 adapter、训练来源哈希及数据 manifest 哈希；只验证端口可连接不够。

本地服务对 `json_object` 和 `json_schema` 请求使用LM Format Enforcer逐token约束，并在生成后再次校验完整JSON/Schema；普通文本请求保留原始解码方式。平台依据所选能力注册表生成参数名和类型约束，并将用户请求中明确给出的参数值绑定为相应约束，包含数值、数组、向量和联合类型；不注入参考标签、参考脚本或推导QoI答案。其余内容仍由模型生成。结构合法不代表语义正确，事件幅度、参数保留和物理执行检查继续生效。响应记录约束方法及Schema哈希，失败原始输出保留。

以下是可选的完整69条注册表增强验证复现命令；本次收尾没有执行新一轮69条，不预设它会通过。需模型资产、本机服务和新的输出目录：

```powershell
.venv/Scripts/python.exe scripts/evaluate_modelscope_taskspec.py --model Qwen2.5-Coder-1.5B-SatAgent-v5-r47-explicit-literals --base-url http://127.0.0.1:8033 --dataset-dir data/training/taskspec_lora_v5_current_prompt_event_aligned --all-validation --registry-constraints --output-dir reports/v5_reproduction_all69
```

去掉 `--all-validation --registry-constraints`、改用新输出目录，可执行各场景族原始提示检查；使用 `--product-chain --max-repair-attempts 1`、再改用新目录，执行生产创建与修改链。默认的六条族代表检查不能当成完整 69 条结果。还需要独立热环境生产脚本和实际模型网页流程，自动队列已包含前者。

旧 v3 为 68/69 通过、整批 FAIL：电池容量损失 0.3 被模型生成为 1.0，门禁拒绝执行。新模型不得覆盖这个原始结果；任何格式修复、确定性字段绑定与模型原始准确率都应分别披露。

## 网页使用

在“模型服务”设置填写服务的 `/v1` 地址，发现模型、保存并运行完整探测。请求采用300秒客户端超时及 `json_schema`，输出token上限以当前配置为准。成功后用自然语言创建或修改任务，检查参数与事件，再生成和下载Python。

网页验收必须实际执行下载脚本，并验证原生遥测、结果判据及封存制品；仅看到代码或绿色探测提示不足以判定完整功能通过。独立入口 `reports/local_requirements_completion/run_actual_modelscope_browser.py` 已实际执行；后续可视区域增强入口 `run_actual_modelscope_browser_visible_r11.py` 还要求代码／结果目标实际入镜，并补拍真实模型结果的曲线与图例。各次结果和源码版本分别记录。

<!-- Historical state records below apply to their named revisions; latest acceptance is linked above. -->
## 数值约束与纠错的补充说明（2026-10-30）

结构化 JSON 生成不等于参数满足物理范围。当前服务在解码阶段增加标量数值边界门控，并在生成完成后继续执行完整 JSON Schema 校验；客户端对可解析的 Schema 拒绝响应最多请求模型纠错一次，保持相同约束，分别保存 `model_call_rejected.json`、`raw_model_output_rejected.txt` 与最终响应。不会通过钳位参数、放宽范围或替换回答获得通过结果。

该修订的真实模型、原生脚本执行和完整应用复验保留原始记录；热环境失败、纠错仍失败、中文字符表传播缺陷和主动中止的部分回归也保留。最新状态以[需求报告](../reports/current_requirements_status.md)为准；协议测试、产品链路实测与原始模型准确率分别记录。24小时原自动资格未通过，用户选择的独立原始证据复核已通过，不再标为仍在等待。
