# RW_JAM 工程仿真基线准备度

训练与评测候选数据默认且仅允许 Basilisk 原生路径。准备度门要求原生指令力矩、电机电流、冻结的执行器参数合同、18 案例原生门、签名冻结和 Pair 具名审批。缺少任一条件时系统保持阻断，不得使用代理数据替代。

当前工程仿真合同已冻结为 `J=0.015 kg*m^2`、`Kt=0.02 N*m/A`、
`I_limit=10 A`，范围为 `engineering_simulation_only`。它不代表真实硬件、
硬件标定或飞行验证。原生 18/18 案例、9/9 物理配对和 9/9 诊断候选门
已通过；3 项签名已冻结，9 个 Pair 已具名批准，readiness 为
`READY_FOR_ASTROGRAPH_PROMOTION`。

执行：

```bash
python scripts/assess_rw_jam_readiness.py \
  --native-gate-report reports/rw_native_validation_report.json \
  --dataset-root reports/rw_native_validation \
  --output reports/rw_jam_readiness.json
```

默认加载包内工程仿真合同；也可使用 `--actuator-contract` 指定兼容合同。
使用 `--native-gate-report` 从正式候选门报告推导已执行案例数，避免手工声明。
退出码 2 表示准备度条件未满足，不表示脚本异常。

该状态只批准内部工程仿真、训练和评测，不代表真实硬件、硬件标定、地面试验相关性或飞行验证。
