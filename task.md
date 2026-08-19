# 三目标实施任务

日期：2026-07-29

当前进度：阶段 0 至阶段 8 全部完成，`G0` 至 `G7` 全部关闭。31 对象、93 个内部工程场景、自然语言 279/279 接受与 10/10 拒绝、经验池和受控复用、真实 vLLM 纵向 A/B、规模/容量、安全与恢复矩阵均通过。最终环境、Doctor 12/12、全量 641/641、Ubuntu 平台、vLLM 3/3、回退 0 和 pip-audit 0 漏洞均 PASS，工程清理完成。所有结论仅用于内部工程仿真、训练和评测，不代表真实硬件或飞行验证。

## 总原则

- 按对象和场景纵向闭环，不按文件数量推进。
- 每批改动都必须完成合同、运行、物理证据、自然语言和回归测试。
- 新增经验能力先做只读候选，再做具名审批后的受控复用。
- 当前工程基线边界保持不变。
- 所有结果仅属于内部工程仿真，不代表真实硬件、硬件标定或飞行验证。

## 阶段 0：冻结范围与基线

任务：

1. 生成 24 部件、6 分系统、1 整星的机器可读对象清单。
2. 输出主 Capability、模式、adapter、script export、输出和物理门覆盖矩阵。
3. 为每个对象提出常见故障/退化清单及 FMEA/工程依据。
4. 由领域审查冻结场景最小集、参数范围和 `N/A`。
5. 建立 PR 代表集、夜间全量集和性能预算。

交付物：

- `configs/acceptance/object_scope.json`
- `configs/acceptance/scenario_baseline.json`
- `reports/object_scenario_coverage.json`
- `reports/object_scenario_coverage.md`

完成门：`G0_SCOPE_BASELINE`

## 阶段 1：补齐部件主能力

优先批次：

1. 推进/EPS：`fuel_tank`、`thruster`、`pdu`。
2. ADCS 传感与执行：`cmg`、`imu`、`magnetometer`、`star_tracker`、`sun_sensor`。
3. 通信：`antenna`、`transmitter`、`ground_station`。

每个对象必须：

- 选择或新增 active 主 Capability；
- 使用现有源模型或受审查的新模型，不调用 demo runner；
- 提供正常模式、确定性脚本导出和直接输出；
- 接入工作台、Agent、CLI、打包和测试。

完成门：24/24 部件有默认主能力。

## 阶段 2：补齐部件故障与退化

先为 8 个已有正常主能力的部件补模式，再处理阶段 1 新主能力：

`data_queue`、`heater`、`link_budget`、`onboard_storage`、`payload`、`payload_sensor`、`power_sink`、`thermal_node`。

实施结果：第一批 8 个和阶段 1 新增的 11 个主能力均已完成；连同原有 5 个三模式部件，当前部件覆盖为 24/24。场景候选集仍需通过 `G0_SCOPE_BASELINE` 的具名领域审批后才能称为“批准场景”。

每个 effect 必须实现：

- owner 和 aliases；
- 参数 Schema、范围和来源；
- onset/duration；
- runtime delivery；
- direct effect evidence；
- nominal/fault/degradation 配对测试；
- 泄漏和 Claim 边界。

完成门：24/24 部件的批准三模式场景通过。

## 阶段 3：补齐分系统与整星传播

按以下顺序推进：

1. EPS：电池、太阳阵、PDU 故障与效率/容量退化。
2. Propulsion：推力器失效、推力/比冲下降、燃料泄漏/压力下降。
3. Comm/Data：发射机/链路中断、存储/队列异常和链路退化。
4. Payload：载荷关闭、数据率/功耗/测量质量退化。
5. Thermal：加热器失效和散热退化。
6. ADCS：保留现有基线并扩充传感器、执行机构常见场景。
7. 整星：验证每个分系统事件的跨系统传播和资源反馈。

要求每个分系统场景至少有一组相同种子的 nominal/fault 或 nominal/degradation 配对。

实施结果：6/6 分系统均具备三模式独立配对证据；整星运行时已覆盖 ADCS、EPS、Propulsion、Comm/Data、Payload、Thermal 六类代表事件的跨边界传播。证据见 `reports/cross_subsystem_propagation_matrix.md`。候选场景仍须通过具名领域审批后才可称为“批准场景”。

完成门：`G1_CONTRACT_COVERAGE` 和 `G2_RUNTIME_PHYSICS`。要求 24/24 部件、6/6 分系统、1/1 整星合同完整，全部批准场景与耦合传播证据通过。

## 阶段 4：自然语言端到端矩阵

当前实施结果：

- 中文、英文、关键术语别名接受矩阵：279/279 PASS，覆盖 31 个对象的正常、故障、退化请求；
- 每个通过案例均完成 TaskSpec 严格校验、确定性脚本执行、Run Bundle 密封和物理 ValidationOutcome；
- 模板/规则回退为 0，未知 Capability/effect 产生率为 0；
- 歧义、越权、未知能力/effect、过度声明和提示注入结构化拒绝矩阵：10/10 PASS，false accept、脚本生成和模型调用均为 0；
- `G3_NL_E2E` 已关闭。

任务：

1. 从场景基线自动生成中文、英文和别名请求。
2. 添加参数修改、输出选择、耦合、多轮补充和歧义请求。
3. 添加未知能力、未知效果、越权、过度声明和提示注入。
4. 使用真实 vLLM 生成 TaskSpec。
5. 对接受案例导出脚本并实际运行。
6. 验证 Run Bundle、物理门和 ClaimReport。
7. 保存逐案例模型调用与零回退证据。

核心指标：

- 接受案例 TaskSpec 严格有效率 100%；
- 脚本执行成功率 100%；
- 物理 ValidationOutcome PASS 率 100%；
- 拒绝案例结构化拒绝率 100%；
- 模板/规则回退 0；
- 未知 Capability/effect 产生率 0。

完成门：`G3_NL_E2E`

## 阶段 5：Experience Record 与 Store

实施结果：

- 新增版本化 Experience Record JSON Schema 与不可变 Pydantic 模型；
- 真实密封 Run Bundle 经原生完整性门后，可自动捕获 TaskSpec、ExecutionPlan、ValidationOutcome、ClaimReport、重试和错误；
- SQLite 元数据、内容寻址制品、稳定去重、冲突检测、递归脱敏、作用域隔离和并发安全审计链已完成；
- `sat-experience` CLI 和 FastAPI 已提供捕获、检索、检查、校验、撤销、保留清理、备份和恢复入口；
- v1→v2 迁移、并发、跨作用域拒绝、哈希篡改、损坏后备份恢复测试共 18/18 PASS；
- 使用既有真实密封电池正常工况 Run Bundle 完成实际捕获，经验完整性复验 PASS；
- `G4_EXPERIENCE_RESERVOIR` 已关闭。

任务：

1. 新增 Experience Record JSON Schema 和 Pydantic 模型。
2. 从 Agent、Planner、Runner、Validation、用户反馈捕获不可变轨迹。
3. 实现 SQLite 元数据、内容寻址制品和审计事件。
4. 实现去重、冲突检测、撤销、保留策略和敏感字段脱敏。
5. 提供 CLI/API：capture、list、inspect、verify、revoke、backup、restore。
6. 添加并发、迁移、损坏恢复和多作用域隔离测试。

完成门：`G4_EXPERIENCE_RESERVOIR`

## 阶段 6：受控经验编译和复用

实施结果：

- 仅从未撤销且哈希验证通过的密封 Run Bundle 经验编译候选 lesson；
- lesson 只允许参数/输出解释、失败恢复建议、TaskSpec/DAG 哈希示例三类，不生成代码；
- held-out 评测通过后，必须由两名不同具名审批者批准，且编译者和评测者不得自批；
- 仅活动快照中的 approved、未撤销、同作用域和版本兼容 lesson 可进入 Agent；
- Agent 保存 lesson ID、哈希、命中能力和 `presented_to_model` 影响归因，Registry、Validator、Planner、Claim Guard 保持权威；
- 支持一键禁用、指定快照回滚和即时撤销；
- 专项测试 28/28、相关 Agent/API 回归 50/50、正式受控经验验收 18/18、脚本治理 0 错误；
- `G5_CONTROLLED_REUSE` 已关闭。

任务：

1. 从失败和成功轨迹生成结构化候选 lesson，不生成代码。
2. 验证候选 lesson 的证据引用和适用范围。
3. 使用 held-out 案例评估候选经验。
4. 实现具名 APPROVE/REJECT/REVOKE。
5. 只检索 approved、未撤销、版本兼容的经验。
6. 将经验作为 advisory context 注入 Agent，并记录具体影响。
7. 实现一键禁用经验和快照回滚。

完成门：`G5_CONTROLLED_REUSE`

## 阶段 7：性能、安全与可靠性

实施结果：

- 真实 vLLM 固定种子纵向 A/B 6/6 Token 改善，`p=0.03125`，两组物理运行均 100% PASS，回退为 0；
- vLLM 279 例证据基准、经验检索 1k/10k/100k 和容量矩阵 47/47 PASS；
- 投毒、注入、越权、篡改、撤销/回滚攻击矩阵，31/31 对象确定性矩阵均 PASS；
- vLLM 中断显式恢复、Worker 崩溃重领与真实执行均 PASS；
- 正式聚合门 `reports/reliability_acceptance/report.json` 为 8/8 PASS，`G6_LONGITUDINAL_EVAL` 已关闭。

性能：

- 运行 PR 代表矩阵和夜间全量矩阵；
- 测量 vLLM p50/p95、Token、重试、GPU 服务状态；
- 测量 1k/10k/100k 经验检索；
- 验证最小步长、最长任务、批量和事件密度。

安全：

- 经验投毒、提示注入、反馈操纵、跨作用域读取；
- 哈希篡改、越权晋级、撤销后继续命中；
- 未知 Capability/effect 和任意脚本注入。

可靠性：

- 经验库备份恢复、Schema 迁移和损坏恢复；
- 固定快照重复执行；
- Worker/vLLM 中断与恢复；
- 纵向收益、后向保持、最坏类别回归和成本转移。

完成门：`G6_LONGITUDINAL_EVAL`

## 阶段 8：最终复验

实施结果：

- uv 外部指定环境安装和 `pip check` PASS，稳定发布身份为 `0.7.8`；
- 严格 Doctor 12/12、严格 release-check 12/12 PASS；
- 全量监督回归 109/109 文件、641/641 测试 PASS，返回码 0、无跳过；
- Ubuntu 跨平台入口 PASS，真实 vLLM 代表案例 3/3，模板/规则回退 0；
- 新鲜 `pip-audit` 扫描 85 个锁定依赖，0 漏洞、返回码 0；
- 历史纵向运行、旧回归、重复报告、版本试验后缀、缓存、字节码和重复虚拟环境已清理；当前原始验收证据保留；
- `G7_FINAL_ACCEPTANCE` 已关闭。

顺序：

1. `uv sync` 和 `pip check`。
2. 严格 Doctor 12/12。
3. 全量 PyTest。
4. 31 对象批准场景矩阵。
5. 真实 vLLM 自然语言端到端矩阵。
6. 经验 A/B、回滚、安全和恢复矩阵。
7. 平台验收、`pip-audit`、脚本治理和源码洁净检查。
8. 更新 release manifest、指南和最终报告。

完成门：`G7_FINAL_ACCEPTANCE`

## 执行依赖

```text
G0 范围冻结
  -> G1 合同覆盖
  -> G2 运行与物理覆盖
  -> G3 自然语言端到端
  -> G4 经验池
  -> G5 受控复用
  -> G6 纵向/四维评测
  -> G7 最终验收
```

任何阶段出现物理真实性、安全或回滚失败时，不得通过扩大模板回退、降低阈值或删除场景来关闭门禁。
