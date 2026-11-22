# #202 电池五测点原始时序对照入口

当前仓库只有外部仿真统计摘要，没有 `10.csv` 原始时序或经参考方确认的五列映射。此入口在这些材料缺失时拒绝比较，不产生新的“通过”结论。它不修改热模型、参数或原定容差。

## 参考方需交付

1. `10.csv` 原始五路温度及相对仿真时间列，完整覆盖 0–3540 s、每 0.3 s 一点。提供文件 SHA-256、时间零点、单位及坏值/不同步规则。
2. 以 `configs/reference_alignment/battery_five_point_mapping.template.json` 填写五路的列名、共同电池包编号、测点位置、温度定义、集总权重和物理依据。若有逐路有效性标志列，用 `quality_policy=per_channel_valid_flags` 并填写 `valid_column`；若全部点已审查有效，用 `all_rows_reviewed_valid`。`status=CONFIRMED_BY_REFERENCE_OWNER` 及 `mapping_evidence_uri` 记录确认依据。不同包不得直接合并。
3. 安装与热边界：电池和安装架热容、接触热导与相邻节点、视因子/涂层/受照、充放电耗散及加热器、初温和预热、稳态窗口。详见 [#224 证据包](https://github.com/clonetimer/SatAgentSimulation/pull/224) 中的热侧请求表。

## 执行

在独立输出目录运行（不要改写原始材料）：

```powershell
python scripts/compare_battery_five_point.py `
  --reference-csv path/to/10.csv `
  --mapping-json path/to/confirmed-mapping.json `
  --model-csv reports/external_reference_alignment/model_telemetry.csv.gz `
  --model-statistics reports/external_reference_alignment/baseline_statistics.json `
  --output path/to/new-comparison.json
```

脚本核对原始参考与本模型遥测 SHA-256、五列身份和同一 11,801 点时间网格；先求五列共同有效时刻，再逐时加权合成一个温度序列，最后分别计算均值、总体标准差和线性趋势。本模型用相同时间掩码重算。均值差沿用 ±0.5 °C、总体标准差差沿用 ±0.15 °C；趋势只作诊断，不事后新增通过阈值。出现任何被排除采样，状态为 `INCONCLUSIVE_INVALID_SAMPLES`，等待预先约定有效段规则。映射确认字段仅记录外部声明，程序不能独立证明其真实性；输出 `WITHIN_FROZEN_TOLERANCE` 也不等于硬件标定或任务级科学验收。

