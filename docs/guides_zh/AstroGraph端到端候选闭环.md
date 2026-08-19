# AstroGraph 端到端候选闭环

AstroGraph 回灌适配器使 Sat-Sim 本地 Pipeline 的外部阶段能够接收 AstroGraph 产生的候选结果：

- `ml_model` → `COMPLETED_EXTERNAL`
- `kg_reasoner` → `COMPLETED_EXTERNAL`
- `fusion` → `COMPLETED_EXTERNAL`
- `expert_gate` → 继续保持 `PENDING_EXPERT`

## 不变的可信边界

- Sat-Sim 不签发 AstroGraph 正式诊断；
- 跨项目载荷不得声明 `training_ready=true`；
- 跨项目载荷不得完成具名专家门；
- C 级代理数据不得替代 Basilisk A 级数据；
- 上游任何必需门 `REJECT` 都会累积阻断后续外部阶段。
