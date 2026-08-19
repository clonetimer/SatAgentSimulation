# Basilisk 可信实验与配对验证

## 1. 目标

本版本在 Basilisk 默认正式数据路径之上增加“实验定义—运行记录—配对物理验证—数据资格”的受控闭环。无 Basilisk 代理仍只用于单元测试和接口联调，不能替代正式运行。

## 2. 故障实验模板

平台内置三份受治理的反作用轮实验模板：

- `adcs.rw_friction_increase.basilisk.v1`
- `adcs.rw_jam.basilisk.v1`
- `adcs.rw_torque_authority_loss.basilisk.v1`

模板声明 Basilisk 能力、故障事件、预期遥测响应、故障前配对一致性容差和最少故障前后样本数。模板不执行仿真，也不绕过 TaskSpec 校验。

## 3. 实验记录

每个完成案例新增：

```text
astrograph/experiment_record.json
```

记录内容包括：

- Experiment、Campaign、Pair、Run 和 Dataset 标识；
- Basilisk、平台和 Python 版本；
- 仿真时长、采样周期、求解步长和随机种子；
- 初始参数、环境参数和故障注入定义；
- TaskSpec、Manifest、Trace、Telemetry 和验证报告哈希；
- Git 提交（可用时）及声明边界。

## 4. 配对种子修复

此前数据工厂写入相同配对种子后，通用 Campaign 展开器仍会按案例索引递增种子，导致故障和名义运行实际种子不同。本版本增加显式 `campaign_preserve_seed` 语义：

- AstroGraph 故障/名义配对保留相同种子与初始参数；
- 其他普通 Campaign 继续使用原有按索引递增种子的行为。

## 5. SimulationValidationAgent

该 Agent 是确定性验证器，不是自由决策的大模型 Agent。它检查：

1. Pair ID 和 fault/nominal 角色；
2. 配对种子一致性；
3. 故障发生时刻和故障前后样本数；
4. 故障前遥测一致性；
5. 模板定义的故障后物理信号；
6. Basilisk A 级运行时证据。

输出决策：

- `PASS`：物理签名通过，且 Basilisk A 级证据完整；
- `REVIEW`：物理签名通过，但来源不是 A 级正式证据；
- `REJECT`：至少一个必需检查失败。

验证 Agent 不能修改仿真结果，不能自动审批数据，也不能发布知识图谱知识。

## 6. 训练资格

数据状态分为：

- `formal_training_eligible`：仿真来源满足 Basilisk A 级要求；
- `formal_training_qualified`：配对技术验证通过；
- `candidate_training_ready`：通道、来源和技术验证均通过；
- `training_ready`：上述条件满足，并且专家审核已批准。

因此，技术门通过不等于数据已获准训练。
