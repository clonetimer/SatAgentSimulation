# 获取并使用已微调的模型

源码与权重分别分发。v5适配器从[GitHub Release](https://github.com/clonetimer/SatAgentSimulation/releases/tag/satagent-v5-r47-20261102)获取，固定Qwen2.5-Coder-1.5B-Instruct基座从ModelScope下载，无需重新微调或原开发机器目录。

本机推理要求兼容NVIDIA CUDA环境。无该硬件时，普通离线/实时仿真仍可使用；AI需在工作台配置可访问且通过完整探测的兼容远程服务。开发机器的127.0.0.1不是公共服务。

## 下载模型

在仓库根目录用Python 3.11–3.13执行：

```powershell
python scripts/model_release.py install
python scripts/model_release.py check
```

安装器只用标准库，无需先装torch，自动下载约72.2MB适配器压缩包和约3.10GB基座，核对压缩包及逐文件SHA-256和大小，再生成本机路径配置。模型安装请预留至少4GB额外空间，GPU依赖另需空间。默认目录 `.models/satagent-distributed/`，可加 `--cache-dir` 指定目录。

固定清单在 `configs/models/satagent-v5-release.json`。上游master内容改变时哈希不符会失败，不静默换模型；下载失败不发布半成品，已有安装发现缺项或篡改直接失败、不自动覆盖。check返回 `MODEL_FILES_VERIFIED_NOT_GPU_QUALIFIED`，只证明文件完整，不代表GPU加载和生成通过。

## 准备CUDA环境并启动

已有锁定 `.venv-sft` 可复用。首次配置前需要Python、可用的NVIDIA驱动，以及已安装并加入PATH的uv（先执行 `uv --version` 检查，参见README安装说明）。Windows/NVIDIA配置：

```powershell
uv venv --python 3.12 .venv-sft
python scripts/fetch_torch_wheel.py
uv pip install --python .venv-sft/Scripts/python.exe --find-links third_party/wheels -r requirements-sft-lock.txt
uv pip check --python .venv-sft/Scripts/python.exe
.venv-sft/Scripts/python.exe scripts/model_release.py serve
```

`fetch_torch_wheel.py`从官方索引下载并校验锁定的Windows CPython3.12 CUDA wheel，首次下载较大。仅git clone不含此依赖。实测RTX5060 Laptop、torch2.11.0+cu128；不保证所有显卡/驱动适用。Linux等平台须准备匹配平台的CUDA依赖并独立验证，不使用Windows wheel。

serve先校验模型文件和SFT/CUDA条件，再调用现有 `serve_modelscope_model.py`，保留原模型来源门禁，仅绑定本机。不关闭其他服务，默认8033占用时检查既有进程或加 `--port 8034`，终端保持运行。

另开终端安装并启动工作台：

```powershell
python scripts/setup_uv.py
.\scripts\start_local_windows.ps1
```

在“模型服务”填写地址 `http://127.0.0.1:8033/v1`、模型名 `Qwen2.5-Coder-1.5B-SatAgent-v5-r47-explicit-literals`，本机Key留空。发现模型、保存并完整测试，再创建/修改需求、审阅参数、生成和执行脚本。发现模型成功不等于生成及执行验收。

## 无网络安装

先在联网机器获取发布ZIP和清单中全部基座文件，将基座按相对路径放到同一目录，然后：

```powershell
python scripts/model_release.py install --offline --archive D:/assets/satagent-v5-r47-lora.zip --base-directory D:/assets/Qwen2.5-Coder-1.5B-Instruct
```

offline不联网，缺少ZIP或基座目录直接失败，哈希门禁不变。仿真资产和GPU依赖仍需各自离线缓存，模型安装成功不等于整套环境离线安装完成。

## 来源与验收范围

发布包保留原11份适配器文件和训练来源的精确字节。原adapter_config.json、README和训练元数据中的旧路径只作历史记录，加载器显式使用新路径。training_args.bin仅作完整哈希追溯，安装和推理不反序列化。基座许可及正式MODEL_CARD.md随包提供。

权重与原v5一致，不重新训练、不改写旧69例严格0/69。既有受约束15次首答和2次界面调用的证据分别保留；生成结果仍需语义门禁、参数审阅和原生结果验证。模型分发的新增验收与既有功能验收基线分别记录，不能将文件下载成功冒充完整生成通过。
