# 软实时交互式卫星仿真器实施任务

日期：2026-08-01  
状态：已完成（RT0 至 RT8 全部 PASS）  
关联规格：`realtime_simulator_spec.md`  
关联清单：`realtime_simulator_checklist.md`

## 1. 当前进度

现有平台批处理基线已通过。RT0 已冻结 31 对象矩阵、四类 Schema、性能/安全策略和兼容快照；RT1 至 RT6 已完成同一 Basilisk 实例分段推进、会话状态机、七档软实时节拍、受控遥控、多速率遥测、人在回路工作台、哈希链证据、Run Bundle 封存和确定性恢复。RT7 正式门完成真实 30 分钟 1x 漂移、4 小时仿真、0.1/0.5/1.0 秒 quantum、0.1x 至 10x、1/2/4 并发、命令/遥测延迟、慢客户端、安全攻击和可靠性矩阵；全部满足冻结预算。RT8 在 feature flag 关闭和开启两侧分别完成 827/827 全量测试，均为零失败、零错误、零跳过；严格 Doctor 12/12、release-check 12/12、Ubuntu 平台、脚本治理、`pip check` 和 0 漏洞依赖审计均 PASS。最终证据见 `reports/interactive/final_acceptance.json`。

本计划只新增扩展能力，不重开或改写原三目标计划，不把规划状态计为产品支持。

## 2. 总原则

- 先验证同一 Basilisk 实例可安全分段推进，再开发 API 和 UI。
- 采用独立 `sat_sim.interactive` 包，避免继续扩大现有 `api.py` 和批处理 Runner 的职责。
- 原 TaskSpec 和 `/runs` 保持稳定；InteractiveSessionSpec 只引用原 TaskSpec。
- 仿真线程是物理状态的唯一写者；网络线程只写有界消息邮箱。
- 每批改动同时完成合同、运行、证据、安全、测试和兼容回归。
- 新功能默认关闭，按 Capability 白名单逐批开放。
- 不为赶节拍丢弃物理步骤，不为提高通过率扩大物理容差。
- LLM 只产生候选配置或候选指令，不进入实时控制环。
- 所有结果仍是内部工程仿真，不代表硬实时、真实遥控或飞行验证。

## 3. 建议目录

```text
src/sat_sim/interactive/
  __init__.py
  models.py              # Session/TC/TM immutable contracts
  state_machine.py       # legal transitions and reason codes
  clock.py               # pacing, rates, drift metrics
  command_catalog.py     # registered target/operation contracts
  command_mailbox.py     # bounded deterministic queue
  telemetry_bus.py       # frame sequencing, buffers, subscribers
  runtime.py             # persistent simulation context protocol
  basilisk_runtime.py    # segmented Basilisk implementation
  manager.py             # session ownership and lifecycle
  evidence.py            # journals, replay and final sealing
  api.py                 # REST/WebSocket router

src/sat_sim/schemas/
  interactive_session.schema.json
  telecommand.schema.json
  telemetry_frame.schema.json
  command_ack.schema.json

tests/interactive/
  test_segmented_runtime.py
  test_session_state_machine.py
  test_soft_realtime_clock.py
  test_telecommand_mailbox.py
  test_command_adapters.py
  test_telemetry_bus.py
  test_performance_benchmark.py
  test_interactive_recovery.py
  test_interactive_api.py

```

生产模块名称可以按现有包约定微调，但会话、命令、遥测、运行时和证据职责不得重新混合为单个大模块。

## 4. 阶段 0：冻结范围与兼容基线

任务：

1. 生成机器可读 `realtime_capability_matrix.json`。
2. 对 31 个对象逐项标记：`controllable`、`telemetry_only`、`unsupported`。
3. 冻结首批 4 个实时主 Capability：整星、ADCS、EPS、Comm/Data。
4. 为每个首批 Capability 列出命令、参数 Schema、单位、权限、输出 stream 和效果字段。
5. 冻结 Session、TC、ACK、TM Schema 和稳定 reason code。
6. 冻结 MVP 性能预算、资源上限和软实时声明文字。
7. 保存当前 641 项测试、Doctor、release-check、API/OpenAPI 和脚本清单快照。
8. 确认默认 feature flag 为关闭，并定义配置优先级。

交付物：

- `configs/interactive/realtime_capability_matrix.json`
- `configs/interactive/performance_budget.json`
- `configs/interactive/security_policy.json`
- 四个 JSON Schema
- `reports/interactive/baseline_compatibility.json`

完成门：`RT0_SCOPE_COMPATIBILITY`

## 5. 阶段 1：分段 Basilisk 可行性原型

任务：

1. 从首批 Capability 中抽取“构建上下文”和“一次性执行”边界。
2. 定义 `PersistentSimulationRuntime` 协议：prepare、advance、apply_command、read_delta、finalize、abort。
3. 先以整星主能力实现同一实例的多次 `ConfigureStopTime + ExecuteSimulation`。
4. 在 quantum 边界读取 recorder 增量，禁止每段重复输出历史行。
5. 实现暂停态不执行 advance，单步只执行指定 quantum。
6. 对一次性和分段执行进行相同 TaskSpec、种子和总时长配对。
7. 验证现有预编排 fault/degradation 在跨 segment 时仍只触发一次。
8. 测量 quantum 0.1、0.5、1.0 秒下的计算余量、抖动和内存变化。
9. 若 Basilisk 重复推进不稳定，停止上层实现并记录真实 blocker，不以周期性重建模型替代持久实例。

必须测试：

- 初始状态、终态和关键遥测配对；
- segment 边界无重复/缺失采样；
- 事件只触发一次；
- 暂停/单步不重建仿真；
- 异常 finalize 释放资源。

交付物：

- `src/sat_sim/interactive/runtime.py`
- `src/sat_sim/interactive/basilisk_runtime.py`
- `tests/interactive/test_segmented_runtime.py`
- `reports/interactive/segmented_runtime_feasibility.json`

完成门：`RT1_SEGMENTED_RUNTIME`

## 6. 阶段 2：会话核心与软实时节拍

任务：

1. 实现不可变 Session Spec、状态快照和状态转换事件。
2. 实现严格状态机和稳定 reason code。
3. 实现 Session Manager，确保每个会话只有一个执行线程和一个物理状态所有者。
4. 实现 paced/unpaced Clock 和 0.1x、0.25x、0.5x、1x、2x、5x、10x。
5. 实现 start、pause、resume、step、rate、stop 和 abort。
6. 状态控制请求进入控制邮箱，并仅在 quantum 安全点生效。
7. 实现会话资源预算、最大运行时间、心跳和空闲回收。
8. 建立运行中工作区和追加式状态日志。
9. feature flag 关闭时不创建 Manager、线程和新 API。

必须测试：

- 所有合法和非法状态转换；
- 并发 pause/resume/step/stop；
- 暂停 sim time 冻结；
- 单步精确性；
- 倍速切换边界；
- 一个会话失败不影响批处理或另一会话；
- feature flag 关闭时现有 OpenAPI 和行为兼容。

交付物：

- `models.py`、`state_machine.py`、`clock.py`、`manager.py`
- `tests/interactive/test_session_state_machine.py`
- `tests/interactive/test_soft_realtime_clock.py`
- `reports/interactive/session_core_acceptance.json`

完成门：`RT2_SESSION_CORE`

## 7. 阶段 3：遥控指令与人在回路

任务：

1. 实现 Telecommand、CommandAck 和命令目录 Schema。
2. 实现有界、线程安全、确定性排序的命令邮箱。
3. 实现 command_id 幂等、哈希冲突拒绝、revision 和过期检查。
4. 实现 viewer/operator/fault_operator/admin 权限。
5. 先实现首批命令：
   - 整星：模式切换或任务级控制；
   - ADCS：姿态目标、控制使能、批准故障注入；
   - EPS：负载切换、保护控制、批准故障注入；
   - Comm/Data：数据生成、下行使能/速率和批准故障注入。
6. 所有命令只作用于注册消息入口或受控 Adapter，不直接暴露任意对象属性。
7. 实现 RECEIVED、VALIDATED、QUEUED、EXECUTING、ACKED、REJECTED、FAILED、EXPIRED 全生命周期。
8. 建立命令哈希链和 actor 审计。
9. 增加自然语言候选命令编译，但默认必须人工确认；不调用任意脚本。

必须测试：

- 正常控制和直接效果遥测；
- 同 ID 同内容幂等；
- 同 ID 不同内容冲突；
- 同时刻顺序确定性；
- 未知、越界、过期、队列满和非法状态；
- 普通 operator 注入故障被拒绝；
- 提示注入和任意代码请求 fail-closed；
- ACK 与实际生效时间一致。

交付物：

- `command_catalog.py`、`command_mailbox.py`
- 首批 Capability 命令 Adapter
- `tests/interactive/test_telecommand_mailbox.py`
- `tests/interactive/test_command_adapters.py`
- `reports/interactive/telecommand_acceptance.json`

完成门：`RT3_TELECOMMAND`

## 8. 阶段 4：实时遥测与网络接口

任务：

1. 实现 TelemetryFrame、SessionEvent 和 StreamGap 消息。
2. 从 recorder 只提取每次 advance 新增的样本。
3. 复用现有 `outputs.telemetry_streams` 和字段合同进行多速率发布。
4. 实现每 stream 单调 sequence、有界环形缓冲和 after_sequence 续读。
5. 实现每客户端有界队列、drop_oldest/断开策略和 gap 计数。
6. 实现 WebSocket 订阅、取消订阅、心跳、认证、最大帧和速率限制。
7. 在 `sat_sim.interactive.api` 中实现独立 Router，并由 `create_app` 在 feature flag 开启时挂载。
8. 保留现有 `/runs/.../telemetry` 运行后 API，不改变返回合同。
9. 会话结束后把实时帧归并到现有多速率遥测制品。

必须测试：

- TestClient WebSocket 实际收帧；
- 快速流和 housekeeping 频率；
- 单调 sequence/sim time；
- 重连补读、窗口外 gap；
- 慢消费者不阻塞仿真；
- 多客户端隔离；
- 非法 stream 和越权订阅拒绝；
- 实时日志与运行后遥测对齐。

交付物：

- `telemetry_bus.py`、`api.py`
- `tests/interactive/test_telemetry_bus.py`
- `tests/interactive/test_interactive_api.py`
- `reports/interactive/telemetry_acceptance.json`

完成门：`RT4_TELEMETRY`

## 9. 阶段 5：交互工作台

任务：

1. 新增独立实时会话导航和视图，原任务中心仍为默认入口。
2. 实现会话列表、状态、sim time、墙钟漂移和连接状态。
3. 实现启动、暂停/继续、单步、停止和倍速控制条。
4. 用命令 Schema 渲染指令表单，不接受原始 JSON 以外的隐式字段。
5. 展示 ACK/NACK 时间线、执行时间和 reason code。
6. 实现遥测 stream 选择、最新值、曲线、表格和 lag/gap 指示。
7. 危险指令、故障注入和停止需要二次确认。
8. 浏览器断线后允许重连，不终止仿真会话。
9. 桌面和移动视口均验证无重叠、文本溢出和控制抖动。

必须测试：

- API 合同测试；
- 浏览器端会话全流程；
- 暂停后 UI 时间冻结；
- 重连后状态和遥测续接；
- 权限差异；
- 桌面/移动截图和长文本；
- 原工作台回归。

交付物：

- 现有 Web Workbench 的独立实时模块
- `tests/interactive/test_interactive_workbench.py`
- `reports/interactive/workbench_e2e.json`

完成门：`RT5_HUMAN_IN_LOOP`

## 10. 阶段 6：证据、恢复与经验接入

任务：

1. 实现 Session、状态、命令、ACK、遥测和节拍日志哈希链。
2. 明确工作区未密封状态，禁止运行中被 Experience Store 当作 verified 来源。
3. 实现正常停止后的 flush、验证、Claim Guard 和现有 Run Bundle 封存。
4. 在最终报告中关联 command_id、ACK、生效时间和遥测效果窗口。
5. 实现中断识别和 `INTERRUPTED` 终态。
6. 实现从初始 TaskSpec、固定种子和 ACK 命令日志的确定性重放。
7. 重放一致后创建新 recovery epoch；不篡改旧日志。
8. 篡改、版本漂移、Capability 缺失或重放不一致时 fail-closed。
9. 只有最终密封且 ValidationOutcome PASS 的会话可以进入现有 Experience Store。

必须测试：

- 正常封存和完整性；
- 强制终止不伪造 PASS；
- API/Worker 崩溃识别；
- 重放语义一致；
- 指令/遥测/状态日志篡改；
- 旧 Capability 版本不可静默恢复；
- 经验捕获只接受密封结果。

交付物：

- `evidence.py`
- 交互式证据 manifest
- `tests/interactive/test_interactive_recovery.py`
- `reports/interactive/evidence_recovery_acceptance.json`

完成门：`RT6_EVIDENCE_RECOVERY`

## 11. 阶段 7：性能、安全与可靠性

性能任务：

1. 运行 quantum 0.1/0.5/1.0 秒基准。
2. 运行 0.1x 至 10x 倍速矩阵并记录实际可达率。
3. 单会话 30 分钟门和 4 小时长稳门。
4. 1、2、4 会话并发容量和资源预算。
5. 10 Hz 与聚合 100 frame/s 遥测吞吐。
6. 正常、慢速和重连客户端背压矩阵。

安全任务：

1. 未认证、越权、跨会话、重放、冲突 ID 和 revision 攻击。
2. 任意代码、路径穿越、未知模型字段、超大消息和洪泛。
3. 普通 operator 故障注入、LLM 提示注入和确认绕过。
4. 日志与恢复证据篡改。

可靠性任务：

1. 控制竞态、API 重启、Worker 崩溃和客户端断线。
2. 队列满、过期、乱序和重复命令。
3. 资源释放、孤儿线程、端口、文件和 SQLite 锁检查。
4. 相同输入、种子和命令日志重复运行。
5. 批处理与分段执行配对保持。

交付物：

- `configs/interactive/performance_budget.json` 冻结版
- `reports/interactive/performance_benchmark.json`
- `reports/interactive/security_acceptance.json`
- `reports/interactive/reliability_acceptance.json`

完成门：`RT7_PERFORMANCE_SECURITY`

## 12. 阶段 8：最终复验与发布

执行顺序：

1. feature flag 关闭，运行现有全量测试和发布门。
2. feature flag 开启，运行现有全量测试和全部实时专项测试。
3. 执行首批 4 Capability 的人在回路端到端矩阵。
4. 执行暂停、单步、倍速、动态遥控、多速率遥测和最终封存。
5. 执行性能、安全、恢复和长稳矩阵。
6. 执行 `pip check`、严格 Doctor、release-check、平台验收和 `pip-audit`。
7. 更新 OpenAPI、CLI 帮助、工作台、开发/用户/运维文档和发布清单。
8. 输出支持矩阵和已知边界，不把 telemetry-only 对象列为 controllable。

最终交付物：

- `reports/interactive/final_acceptance.json`
- `reports/interactive/final_acceptance.md`
- `reports/interactive/realtime_capability_matrix.json`
- 更新后的产品文档和 release manifest

完成门：`RT8_FINAL_ACCEPTANCE`

## 13. 回归策略

每个阶段至少执行三层回归：

1. **专项层**：只运行新增 interactive tests。
2. **邻接层**：API、Worker、队列、Run Bundle、多速率遥测、统一运行时和 Web Workbench。
3. **全量层**：现有 641 项及后续新增测试全部执行，0 skipped、返回码 0。

关键兼容断言：

- feature flag 关闭时 `/runs` 行为和 OpenAPI 既有路径不变；
- 批处理 Adapter 不依赖 Session Manager；
- 原 TaskSpec Schema 不新增必填项；
- 原脚本导出仍调用确定性批处理入口；
- 原密封 Run Bundle 校验器不接受未完成会话工作区；
- Agent、经验检索和 vLLM 路径不因实时线程存在而改变。

## 14. 回退策略

- 实时功能以一个总 feature flag 和 Capability 白名单控制。
- 任一实时 Gate 失败时，可关闭实时入口而不回退批处理修复。
- Schema 在正式发布前保持 experimental namespace；发布后只做向后兼容扩展。
- 工作台实时模块加载失败时不影响任务中心和运行报告。
- 数据库迁移采用独立 interactive 数据库或独立表前缀，升级前备份，迁移失败不修改现有任务库。
- 不删除失败会话证据；以 ABORTED/FAILED/INTERRUPTED 明确保留。

## 15. 依赖关系

```text
RT0 范围与兼容冻结
  -> RT1 分段执行可行性
  -> RT2 会话与时钟
  -> RT3 遥控
  -> RT4 遥测
  -> RT5 人在回路工作台
  -> RT6 证据与恢复
  -> RT7 性能/安全/可靠性
  -> RT8 最终复验
```

`RT1` 是停止线。未证明持久 Basilisk 上下文的分段执行正确性前，不应先构建完整 UI、协议或大规模命令目录。
