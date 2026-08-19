# 诊断 Pipeline 运行闭环

## 1. 定位

诊断 Pipeline 将静态合同落实为受控运行时。卫星仿真平台负责生成和校验仿真证据，不替代 AstroGraph 的模型、知识推理、融合及最终诊断治理。

## 2. 本地可执行阶段

- 数据质量与故障/名义配对验证；
- 诊断签名检测；
- 遥测特征提取；
- 候选 Simulation Evidence Graph 构建。

## 3. 外部交接阶段

以下阶段只生成结构化交接请求，不按配置字符串加载代码：

- 数据驱动模型；
- Neo4j 因果推理；
- 诊断融合；
- 具名专家审核。

## 4. 运行接口

API：

- `GET /diagnostics/pipelines`
- `GET /diagnostics/pipelines/{fault_id}`
- `POST /diagnostics/pipelines/{fault_id}/execute`

CLI：

```bash
python scripts/run_diagnostic_pipeline.py \
  --fault-root <fault_case_dir> \
  --nominal-root <nominal_case_dir>
```

## 5. 可信边界

- 仿真真值和 `label.*` 不得进入模型特征；
- 缺少物理参数的派生特征必须阻塞，不得经验填补；
- 证据图仅为候选投影，不能自动合并至正式 Neo4j；
- 卫星仿真平台不签发正式诊断，AstroGraph 是最终发布权威；
- 正式诊断仍要求 Basilisk A 级数据、独立 Benchmark、冻结签名和具名专家审核。

## 6. 当前状态

RW 原生 18 案例门和受治理诊断候选门已通过。当前工程基线允许内部训练和评测，但模型发布、知识图谱正式合并及真实硬件外推仍受各自独立治理门约束。
