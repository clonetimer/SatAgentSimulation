# AstroGraph 反作用轮故障数据集工厂

版本：`sat-sim.astrograph-fault-dataset-factory.v1`

## 目标

本工厂为 AstroGraph 生成可审计的**工程仿真数据**。它复用 sat-sim 的标准 Campaign、TaskSpec、统一执行和 Dataset Writer，不建立第二套仿真执行链。

首批覆盖三个 AstroGraph 故障标签：

- `ADCS_RW_FRICTION_INCREASE` → `adcs_rw_friction_increase`
- `ADCS_RW_JAM` → `adcs_rw_jamming`
- `ADCS_RW_TORQUE_AUTHORITY_LOSS` → `adcs_rw_torque_authority_loss`

其中力矩权限下降是独立的性能退化事件，不能用二值电机完全失效替代。

## 配对与划分

每个故障运行都有一个名义配对运行。两者使用相同的：

- 随机种子；
- 初始姿态、角速度和轮速；
- 控制器参数；
- 目标轮。

默认每种故障生成 9 个训练、3 个验证、3 个测试独立运行，共 45 个故障案例和 45 个名义配对案例。

## AstroGraph Sidecar

每个标准 sat-sim 案例目录额外写出：

```text
astrograph/
├── telemetry.csv
└── dataset_contract.json
```

总目录额外写出：

```text
astrograph_dataset_index.json
```

遥测边界会把实际目标轮归一化为 AstroGraph 的 `_0` 目标部件通道，但在契约中保留原始目标轮号和来源字段。

## 当前限制

- 默认生产者为非 Basilisk 的确定性 ADCS 工程代理，不等同于高保真或飞行验证数据。
- 现有仿真未输出反作用轮电机电流，冻结的六通道 LSTM 候选因此仍标记为 `training_ready=false`。
- `friction_torque` 来自工程代理状态，不是实测传感器值。
- 数据入库、模型训练和知识图谱发布仍须经过独立验证与专家审核。

## 执行

```bash
PYTHONPATH=src:. python scripts/generate_astrograph_rw_fault_dataset.py \
  --config configs/integration/astrograph_rw_dataset_factory_v1.json
```

只检查完整 90 案例计划：

```bash
PYTHONPATH=src:. python scripts/generate_astrograph_rw_fault_dataset.py --dry-run
```
