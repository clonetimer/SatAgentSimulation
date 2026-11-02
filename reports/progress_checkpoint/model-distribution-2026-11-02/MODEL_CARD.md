# SatAgent v5 LoRA 发布包

用途：配合SatAgentSimulation项目的注册能力、用户显式参数约束、语义校验和原生执行链，创建和修改仿真TaskSpec。由ModelScope来源的Qwen/Qwen2.5-Coder-1.5B-Instruct实际LoRA微调得到；r=16、alpha=32，588条训练/69条验证，实际222步、3轮。不是通用聊天模型或飞行级决策模型。

本包包含原始适配器、分词器和training_provenance.json的精确字节，另附固定基座文件清单和基座Apache-2.0许可。基座权重由安装器从ModelScope独立获取，不能仅凭本包推理。历史adapter_config.json/README及训练来源中的绝对路径是原始记录；启动器明确提供当前机器的新基座和适配器目录，不依赖原机器。training_args.bin仅为原训练文件的完整哈希追溯保留，安装和推理均不反序列化它。

实测边界：既有15次真实首答完成原始脚本执行及密封复核，另有2次真实安装态创建/修改调用。旧69例严格0/69、原回答诊断54/69、最终对象严格0/69保留，不将15例改写成69/69，不声称任意自然语言可靠。物理覆盖PARTIAL，使用者需审阅参数、事件和原生结果。本次分发不重新训练或改变权重。

安装入口：拉取项目源码后运行 `python scripts/model_release.py install`，通过固定发布清单下载和验证本包及基座。使用兼容NVIDIA GPU和requirements-sft-lock.txt所列环境执行 `python scripts/model_release.py serve`，再在工作台配置 `http://127.0.0.1:8033/v1` 和发布清单中的模型名。模型检查通过只证明文件身份；GPU加载和实际任务执行需另行验证。

来源与使用条件：基座许可证全文见LICENSE，保留Qwen原版权和许可说明。模型使用边界和来源按本模型卡及项目验收记录披露。项目源码许可与基座许可应分别查阅，不因本包附基座许可而混同。
