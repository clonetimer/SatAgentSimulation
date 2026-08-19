# 故障机理、遥测特征与诊断 Pipeline 映射

本协议建立卫星仿真平台与 AstroGraph 之间的受治理语义映射：

```text
故障模式 → 故障机理 → 传播链 → 可观测量 → 特征合同 → 诊断 Pipeline
```

## 1. 已纳入的故障

- `ADCS_RW_FRICTION_INCREASE`
- `ADCS_RW_JAM`
- `ADCS_RW_TORQUE_AUTHORITY_LOSS`
- `EPS_BATTERY_CAPACITY_LOSS`

前三项为 provisional 合同，仍依赖 18 案例 Basilisk 原生校准。EPS 电池容量损失仅完成跨系统合同映射，因 Basilisk EPS 原生能力尚未迁移容量损失事件而保持阻塞。

## 2. 目标泄漏约束

`simulator_truth` 只允许用于物理审核，不得作为模型输入。缺少的 AstroGraph 主通道必须显式记录，禁止通过常数、标签或故障注入状态伪造。

当前缺失：

- 反作用轮电机电流；
- 反作用轮指令力矩；
- EPS 电池容量估计与 SOH 估计。

## 3. 模型绑定边界

Pipeline Registry 只定义处理阶段和候选模型族，不启用任何正式模型绑定。启用绑定至少需要：

1. A 级 Basilisk 数据；
2. 独立训练、验证、测试运行；
3. Release Gate 通过；
4. 具名专家审核。

## 4. AstroGraph 协议导出

执行：

```bash
python scripts/export_astrograph_diagnostic_mapping.py \
  --output reports/diagnostic_mapping_bundle.json
```

导出包包含每项映射的 SHA-256、模型输入允许通道、缺失主通道、Pipeline 绑定状态与治理边界。
