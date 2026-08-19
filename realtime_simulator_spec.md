# 软实时交互式卫星仿真器规格

日期：2026-08-01  
状态：规划基线  
适用项目：卫星仿真 Agent 平台 `0.7.8`

## 1. 目标

在不改变现有批处理仿真行为和证据语义的前提下，新增一个软实时、人在回路的交互式仿真器：

1. 用户可以创建常驻仿真会话，并执行启动、暂停、继续、单步、停止和倍速切换。
2. 会话运行期间可以接收受控遥控指令，完成校验、排队、执行和 ACK/NACK 回执。
3. 会话运行期间可以按多速率持续发布遥测帧，支持实时显示、断线续读和运行后查询。
4. 复用当前 Capability、物理模型、事件合同、ValidationOutcome 和 Claim Guard，不建立第二套物理真相。
5. 会话结束后生成与现有运行兼容的密封 Run Bundle、ValidationOutcome、ClaimReport 和审计证据。

## 2. 兼容性原则

- 原有 `TaskSpec 1.0.0`、`/runs` API、CLI、批处理 Runner、Worker 和脚本导出保持兼容。
- 新能力使用独立的 `InteractiveSessionSpec` 和 `/interactive/sessions` 命名空间，不给现有 TaskSpec 增加必填字段。
- 默认关闭实时功能；`SAT_SIM_REALTIME_ENABLED=false` 时，不启动会话管理器、后台线程或实时端点。
- 现有 Adapter 仍可独立执行。实时 Adapter 是附加入口，不能替换默认批处理路由。
- 不修改已密封 Run Bundle。运行中的会话写入独立工作区，只有停止并完成验证后才转为密封运行包。
- 新代码不得让现有全量测试、Doctor、release-check、平台验收或 vLLM 验收下降。
- 实时功能失败时只终止对应会话，不影响批处理 API、队列 Worker 或其他会话。

## 3. 非目标

- 本扩展全部结果只属于内部工程仿真，不代表硬实时、真实硬件、硬件在环或飞行验证。
- 不承诺硬实时、确定上界调度、飞控闭环认证或实时操作系统能力。
- 不直接接入真实卫星或执行真实遥控，不宣称硬件在环或飞行验证。
- 首版不实现 CCSDS/PUS、CLTU/CADU、SLE、射频调制或信道编码。
- 首版不支持任意 Python、任意寄存器写入或未注册的模型属性修改。
- LLM 不进入周期控制环，不直接执行遥控；自然语言只能生成候选指令，并必须经过确定性校验和可配置的人机确认。
- 首版不尝试序列化 Basilisk 内部对象作为可移植检查点。
- 不要求所有 31 个对象在首个 MVP 中同时具备实时控制面；覆盖按 Gate 逐步扩大。

## 4. 当前基础与缺口

### 4.1 可复用基础

- 24 个部件、6 个分系统、1 个整星对象及其注册 Capability。
- 正常、故障、退化模式和 Basilisk `createNewEvent` 运行时注入机制。
- TaskSpec、ExecutionPlan、Capability Registry、Validator 和 Claim Guard。
- 多速率遥测定义、遥测字段合同、JSONL/CSV 数据集和运行后查询 API。
- FastAPI、本地/远程 Worker、SQLite 持久队列、取消和中断恢复。
- Run Bundle、完整性哈希、ValidationOutcome、ClaimReport 和 Experience Store。

### 4.2 必须新增

- 常驻的仿真会话与确定性状态机。
- 同一 Basilisk 实例的分段推进和墙钟节拍控制。
- 仿真线程安全的遥控指令邮箱与 Capability 命令适配器。
- WebSocket 实时遥控/遥测协议和 REST 会话管理接口。
- 增量遥测抽取、客户端背压、断线续读和缺口显式标记。
- 实时会话工作区、指令日志、遥测日志、恢复回放和最终封存。
- 工作台中的实时会话、控制、遥测曲线和指令回执视图。

## 5. 总体架构

```text
Workbench / External Client
  |-- REST: create, inspect, stop
  |-- WebSocket: control + command + telemetry
  v
Interactive Session API
  v
Session Manager
  |-- Session State Machine
  |-- Soft-Realtime Clock/Pacer
  |-- Telecommand Journal + Mailbox
  |-- Telemetry Publisher + Replay Buffer
  |-- Audit/Event Journal
  v
Realtime Capability Adapter
  v
Persistent Basilisk Simulation Context
  |-- segment execution
  |-- registered command inputs
  |-- existing runtime effects
  |-- recorder deltas
  v
Working Session Bundle
  -> stop/finalize
  -> existing ValidationOutcome and Claim Guard
  -> sealed Run Bundle
  -> optional Experience capture
```

控制面、仿真执行面和遥测发布面必须解耦。FastAPI 线程只写入有界邮箱，不直接修改 Basilisk 对象；只有会话执行线程在安全点读取指令并作用于已注册命令入口。

## 6. 会话合同

### 6.1 InteractiveSessionSpec

```text
schema_version
session_id
task_spec_sha256
capability_id
clock:
  mode: paced | unpaced
  rate: 0.1 | 0.25 | 0.5 | 1 | 2 | 5 | 10
  quantum_s
  start_paused
limits:
  maximum_sim_time_s
  maximum_wall_time_s
  command_queue_capacity
  telemetry_buffer_frames
telemetry_streams[]
security_scope
```

`InteractiveSessionSpec` 引用已通过严格校验的 canonical TaskSpec，不复制或改写物理参数。首版 `clock.mode=paced`；`unpaced` 仅用于快速推进和确定性回放。

### 6.2 状态机

```text
CREATED -> PREPARING -> READY -> RUNNING -> STOPPING -> COMPLETED
                         |        |  ^          |
                         |        v  |          v
                         +------ PAUSED      FAILED
                                  |
                                  v
                                ABORTED
```

允许转换：

- `READY -> RUNNING`：启动；
- `RUNNING -> PAUSED`：在当前 quantum 完成后暂停；
- `PAUSED -> RUNNING`：继续；
- `PAUSED -> PAUSED`：单步一个或指定数量 quantum；
- `READY/RUNNING/PAUSED -> STOPPING`：正常停止并封存；
- 任意非终态到 `FAILED/ABORTED`：结构化失败或管理员强制终止。

非法转换返回稳定 reason code，不隐式修复状态。

## 7. 软实时仿真时钟

### 7.1 时间定义

- `sim_time_s`：权威仿真时间，必须单调递增。
- `wall_time_utc`：观测用墙钟时间，不参与物理计算。
- `rate`：目标 `sim_time_delta / wall_time_delta`。
- `quantum_s`：一次分段推进的仿真时长，也是动态指令生效的最大基本粒度。
- `drift_ms`：实际墙钟进度相对目标节拍的偏差。

### 7.2 分段执行

首个技术门必须证明以下模式对选定 Basilisk 主能力成立：

```text
InitializeSimulation once
repeat:
  apply commands at safe point
  ConfigureStopTime(current_sim_time + quantum)
  ExecuteSimulation()
  read recorder delta
  publish telemetry
```

必须与一次性执行进行同种子配对。最终状态、关键遥测和事件效果在冻结容差内一致，否则不得继续产品实现。

### 7.3 节拍规则

- `rate=1` 时按墙钟节拍等待；仿真计算超时时不得跳过物理步，只记录 lag。
- `rate>1` 表示软实时加速；不能维持目标倍速时降为实际可达速度并显式报告。
- 暂停只停止仿真时间推进，API、心跳和遥控接收仍可工作。
- 倍速修改只在 quantum 边界生效。
- 单步必须推进精确整数个 quantum，且完成后回到 `PAUSED`。
- 不通过丢弃仿真步骤追赶墙钟。

## 8. 遥控指令协议

### 8.1 指令信封

```json
{
  "schema_version": "sat-sim.telecommand.v1",
  "command_id": "tc-unique-id",
  "session_id": "session-id",
  "target": "subsystem.adcs",
  "operation": "set_attitude_target",
  "parameters": {},
  "execute_at_sim_time_s": 125.0,
  "expires_at_sim_time_s": 130.0,
  "expected_session_revision": 12
}
```

### 8.2 生命周期

```text
RECEIVED -> VALIDATED -> QUEUED -> EXECUTING -> ACKED
    |           |          |           |
    +-------> REJECTED   EXPIRED      FAILED
```

每个状态写入追加式日志，至少包含序号、仿真时间、墙钟时间、actor、输入哈希、结果和 reason code。

### 8.3 执行规则

- `command_id` 在会话内幂等；同 ID 同哈希返回原结果，不重复执行。
- 同 ID 不同哈希按冲突 fail-closed。
- 只允许 Capability 命令目录中注册的 `target + operation + parameter schema`。
- 参数范围、单位和可信等级沿用现有 Registry，不接受指令自带声明提升。
- 指令按 `(execute_at_sim_time_s, receive_sequence)` 确定性排序。
- 过期、越权、队列满、未知操作和非法状态必须 NACK，不得静默丢弃。
- 动态故障注入只能调用已批准 effect；正常控制命令与故障注入命令分权限。
- 自然语言候选命令默认需要人工确认，且保存原文、候选、确认者和最终命令哈希。

## 9. 遥测协议

### 9.1 遥测帧

```json
{
  "schema_version": "sat-sim.telemetry-frame.v1",
  "session_id": "session-id",
  "stream_id": "spacecraft_housekeeping",
  "sequence": 384,
  "sim_time_s": 125.1,
  "wall_time_utc": "2026-08-01T00:00:00Z",
  "session_state": "RUNNING",
  "values": {},
  "quality": {
    "valid": true,
    "gap_before": 0,
    "lag_ms": 12.4
  }
}
```

### 9.2 发布规则

- 字段只来自 TaskSpec 输出和 Capability 遥测合同，不允许客户端临时读取任意模型内部属性。
- 每个 stream 独立单调序号；帧携带 `sim_time_s` 和数据质量。
- WebSocket 是实时主通道，现有 REST 遥测查询保留为运行后接口。
- 会话提供按 `after_sequence` 重连续读；超出保留窗口时返回最早可用序号和明确 gap。
- 每客户端使用有界缓冲。慢客户端采用 `drop_oldest` 或断开策略，必须记录丢帧计数，不能阻塞仿真线程。
- 指令回执、会话事件和物理遥测使用不同消息类型或不同逻辑 stream。
- 会话完成后将实时帧归并为现有多速率遥测制品。

## 10. API 与工作台

### 10.1 REST

```text
POST /interactive/sessions
GET  /interactive/sessions
GET  /interactive/sessions/{session_id}
POST /interactive/sessions/{session_id}/control
POST /interactive/sessions/{session_id}/commands
GET  /interactive/sessions/{session_id}/commands
GET  /interactive/sessions/{session_id}/events
GET  /interactive/sessions/{session_id}/telemetry/{stream}
```

### 10.2 WebSocket

```text
WS /interactive/sessions/{session_id}/telemetry
```

连接后由客户端订阅 stream；服务端发送会话状态、遥测、指令回执、gap 和错误消息。认证沿用当前 API 身份体系，不通过 URL query 传长期秘密。

### 10.3 工作台

- 新增独立“实时会话”视图，不改变当前任务中心默认入口。
- 固定尺寸控制条：启动、暂停/继续、单步、停止、倍速选择和连接状态。
- 遥测区域支持 stream 选择、曲线、表格、最新值和 lag/gap 指示。
- 指令区域根据注册命令 Schema 渲染表单，显示 ACK/NACK 时间线。
- 危险指令、故障注入和停止操作必须二次确认。
- UI 刷新不得反向改变仿真节拍；浏览器断线不终止会话。

## 11. 持久化、恢复与证据

### 11.1 运行中工作区

运行中至少持久化：

- canonical TaskSpec 和 InteractiveSessionSpec；
- 会话状态转换日志；
- 遥控指令及回执日志；
- 分段推进记录、时钟漂移和资源指标；
- 增量遥测日志；
- Capability、依赖、模型、代码和配置身份。

工作区明确标记 `UNSEALED_INTERACTIVE_SESSION`，不得作为已验证 Run Bundle 使用。

### 11.2 恢复

- MVP 支持进程异常后识别会话为 `INTERRUPTED`，保留全部日志并禁止虚假续跑声明。
- 正式版本采用“初始 TaskSpec + 固定种子 + 已 ACK 指令日志”的确定性重放恢复。
- 恢复前必须验证所有日志哈希、命令目录版本和 Capability 版本。
- 重放到最后 ACK 的仿真时间后才允许恢复实时接入，并在遥测中标记 recovery epoch。
- 如果重放结果超出容差，结构化失败，不继续会话。

### 11.3 最终封存

正常停止后执行：

```text
flush telemetry
close command journal
final state capture
physical ValidationOutcome
Claim Guard
interactive evidence manifest
existing Run Bundle sealing
optional Experience capture
```

## 12. 安全要求

- 权限至少分为 `viewer`、`operator`、`fault_operator`、`admin`。
- 控制、故障注入、停止、删除分别审计 actor，不允许匿名生产写操作。
- 所有指令使用固定 Schema、大小限制、速率限制和有界队列。
- 禁止任意代码、外部路径、动态 import、shell、未知 Capability/effect 和越界参数。
- WebSocket 连接有认证、来源检查、心跳、空闲超时和最大消息尺寸。
- 会话 ID、命令 ID 和 stream ID 必须防路径穿越；日志脱敏规则沿用 Experience Store。
- LLM 输出只作为不可信候选；确定性 Validator 与人工确认保持权威。
- 指令日志和最终证据使用哈希链，篡改 fail-closed。

## 13. 性能目标

以下是首轮待基准冻结目标，不是当前能力声明：

| 指标 | MVP 目标 | 正式目标 |
| --- | ---: | ---: |
| 单机会话数 | 1 | 4 |
| 最小 quantum | 0.1 s | 0.1 s |
| 本地遥测频率 | 10 Hz | 每会话聚合 100 frame/s |
| `rate=1` 连续运行 | 30 min | 4 h |
| 墙钟漂移绝对值 p95 | <= 200 ms | <= 100 ms |
| 指令接收至入队 p95 | <= 100 ms | <= 50 ms |
| 指令到效果可见 | <= 2 quantum | <= 1 quantum |
| 遥测本地交付延迟 p95 | <= 200 ms | <= 100 ms |
| 静默丢帧 | 0 | 0 |
| 会话异常影响批处理 | 0 | 0 |

如果硬件不能满足目标，必须调整并重新冻结预算，不通过丢物理步、扩大容差或隐藏 gap 关闭性能门。

## 14. 可靠性要求

- 暂停后 `sim_time_s` 不增长，继续后无时间回退或重复步骤。
- 相同 TaskSpec、种子和指令日志产生相同语义结果和命令回执顺序。
- 重复提交、乱序提交、过期指令、队列满和客户端重连均有自动化测试。
- 客户端断线、慢消费者和遥测序列缺口不影响仿真线程。
- Worker/API 重启不得把未密封会话报告为成功。
- 正常停止、超时、失败和强制终止均能释放线程、文件、端口和 Basilisk 资源。
- 运行后报告能够关联每条 ACK 指令与对应遥测效果窗口。

## 15. 首批覆盖

首批只选择能够代表不同控制面的主能力：

1. `whole_spacecraft.unified_native.v1`：整星状态和跨系统遥测。
2. `subsystem.adcs_unified_native.v1`：姿态目标、执行机构和故障注入。
3. `subsystem.eps.unified_native.v1`：负载控制、SOC 和功率保护。
4. `subsystem.comm_data.unified_native.v1`：数据生成、存储和下行控制。

首批通过后，再按 Capability 命令合同扩展到 Propulsion、Thermal、Payload 和部件级会话。没有实时命令合同的对象仍可在会话中提供只读遥测，但不得宣称支持实时遥控。

## 16. 规格追踪门

| Gate | 规格结果 |
| --- | --- |
| `RT0_SCOPE_COMPATIBILITY` | 冻结实时范围、命令/遥测合同、兼容边界和预算 |
| `RT1_SEGMENTED_RUNTIME` | 证明持久 Basilisk 上下文可正确分段推进 |
| `RT2_SESSION_CORE` | 完成会话状态机、软实时节拍和资源隔离 |
| `RT3_TELECOMMAND` | 完成受控遥控、权限、确定性调度和 ACK/NACK |
| `RT4_TELEMETRY` | 完成增量遥测、WebSocket、背压和重连续读 |
| `RT5_HUMAN_IN_LOOP` | 完成人在回路控制和工作台实时视图 |
| `RT6_EVIDENCE_RECOVERY` | 完成证据链、重放恢复、验证和最终封存 |
| `RT7_PERFORMANCE_SECURITY` | 通过性能、安全、长稳和故障恢复门 |
| `RT8_FINAL_ACCEPTANCE` | 新旧功能全部回归并满足发布条件 |

## 17. 发布条件

只有 `realtime_simulator_checklist.md` 的 `RT0` 至 `RT8` 全部 PASS，才可声明“软实时交互式卫星仿真器可用”。发布声明必须同时包含：

- 软实时而非硬实时；
- 内部工程仿真而非真实硬件或飞行验证；
- 已支持的实时 Capability、命令和遥测 stream 清单；
- 实测节拍、延迟、持续时间和并发边界；
- 原批处理功能的完整非回归结果。
