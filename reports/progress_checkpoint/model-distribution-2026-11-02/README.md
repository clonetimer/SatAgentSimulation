# 微调模型分发验收（2026-11-02）

**此前“源码已推送、模型仍仅在本机”的缺口已补齐。** 实际v5适配器已发布到[公开GitHub Release](https://github.com/clonetimer/SatAgentSimulation/releases/tag/satagent-v5-r47-20261102)，下载清单、安装/校验/启动入口和用户文档已提交。模型文件与源码分别分发，使用者无需重新训练。

## 普通用户使用

```powershell
git pull
python scripts/model_release.py install
python scripts/model_release.py check
# 按部署文档准备锁定CUDA环境后：
.venv-sft/Scripts/python.exe scripts/model_release.py serve
```

在工作台配置 `http://127.0.0.1:8033/v1` 和 `Qwen2.5-Coder-1.5B-SatAgent-v5-r47-explicit-literals`，发现、保存并完整测试。详细依赖、离线安装及无GPU远程接入说明见[模型部署文档](../../../docs/model_distribution.md)。源码拉取本身仍不下载权重，需要执行install；无兼容CUDA硬件时不能据文件完整性声称本机推理可用。

## 发布物

| 内容 | 身份与用途 |
|---|---|
| `satagent-v5-r47-lora.zip` | 72,205,734字节；SHA-256 `fbf405b0b396f3cd7aea1858bc3e841f94723d56c0531c70675fc5fa3ef6a9dd` |
| 适配器原文件 | 11份精确原文件，包含73,911,112字节safetensors；均与训练来源清单一致 |
| 包内其他内容 | 原training_provenance.json、固定基座清单、正式MODEL_CARD及基座Apache-2.0许可，共15项载荷 |
| 基座 | ModelScope Qwen2.5-Coder-1.5B-Instruct，10份固定文件合计3,098,972,271字节，独立获取并逐文件校验 |
| 源码与Release标签 | 发布标签 `satagent-v5-r47-20261102` 对应实现提交 `9feaa298b3a8968caa95c3caef77fb3f2a33787b` |

原训练制品和旧验收包不改动。原元数据中的本机路径只作历史追溯；安装器生成当前机器的基座清单和适配器路径，现有模型加载器明确使用新路径。training_args.bin是保留哈希的历史文件，安装和推理均不反序列化，不执行远程Python。

## 验证结果

| 检查 | 实际结果 |
|---|---|
| 分发测试及既有来源/服务/治理合同 | **37通过、0失败、0跳过**；两个警告为既有Starlette提示和刻意重复ZIP条目的测试提示 |
| 安装行为 | 实际文件/HTTP测试覆盖离线安装、迁移路径、错误哈希和大小、ZIP路径穿越/多余/重复/符号链接、损坏缓存不覆盖 |
| 严格脚本治理 | 97个脚本，错误0、警告0 |
| 新路径真实模型加载 | 同一原v5基座和适配器哈希，实际CUDA加载合并成功 |
| 真实生成与原脚本执行 | **1次请求、1次完成、无自动重试、无模板替代**；整星2秒/0.5秒采样需求通过API生成，原始导出脚本SHA不变、退出0，Basilisk原生执行及任务判据PASS |
| 云端拉取 | 禁用Git凭据帮助器，从公开仓库全新克隆成功 |
| 公开网络安装 | 从新克隆执行默认install；实际下载公开Release附件和全部3.10GB ModelScope基座，无本地ZIP/基座目录替代，check及启动命令路径验证通过 |
| GitHub附件 | 服务端大小和SHA-256与本地发布制品一致，非草稿、正确标签和提交 |

真实生成从全新暂存区源码副本和重新安装的模型路径运行；后续公开云端克隆又独立完成全量联网下载，文件身份与前者一致。GPU和原生依赖复用已锁定环境，**没有冒充另一台机器上的全新依赖安装或所有显卡兼容性验证**。模型代码、卫星模型和原语义门禁均未改变；本次新增一条实际生成证据，不扩大原泛化结论。

## 保留的失败和边界

最初测试因安装器尚不存在按预期失败；首次相关回归因新脚本未登记清单失败，登记后37项通过，两次原始记录保留。一次“整星正常仿真”的短请求因能力匹配置信度不足在模型调用前被拒绝，未消耗推理；保留原请求和响应，随后使用明确注册能力完成上述1次调用，不把前置拒绝隐藏为成功。独立代码审查未发现Critical/Important问题，uv前置条件说明已补齐。

匿名GitHub API元数据查询曾遭共享IP速率限制；发布元数据改用已授权API核对，实际公开附件及ModelScope下载仍不带凭据，不依赖该API查询。

本次不重新训练、不重跑24小时、不改写旧69例严格0/69或物理PARTIAL。原[功能验收报告](../maintenance-closure-2026-11-02/README.md)及[24小时独立复核](../manual-endurance-review-2026-11-01/README.md)保持原样。此前2516项为功能验收基线，37项为本次相关范围检查，二者包含重复用例，不直接相加为新的全量回归数字。

本包附精确证据清单与独立校验器。执行以下命令只复核已保存证据，不重新推理、下载或运行仿真：

模型原始回答及其响应哈希在 `model_call.json` 中；`raw_model_output.txt` 保留原Windows文本写入产生的换行字节。校验器分别验证原回答哈希及该确定性换行序列化，不重排或归一化JSON数值，不改写原文件。

```powershell
python reports/progress_checkpoint/model-distribution-2026-11-02/verify_evidence.py
```
