# 软实时交互式卫星仿真器规格

日期：2026-08-01  
项目基线：卫星仿真 Agent 平台 `0.7.8`  
文档性质：新增扩展规格，不替代 `spec.md`

## 1. 目标

在不改变现有批处理仿真、自然语言 Agent、Run Bundle 和验收行为的前提下，新增一个软实时、人在回路、可暂停和倍速运行的交互式卫星仿真器。

目标工作流：

```text
TaskSpec / 场景模板 / Agent 生成结果
  -> 创建交互会话
  -> 初始化常驻仿真内核
  -> 启动、暂停、继续、单步、调速
  -> 接收受控遥控指令
  -> 在确定的仿真时刻执行指令并返回 ACK/NACK
  -> 按多速率遥测合同实时推送数据
  -> 人工持续观察和干预
  -> 停止会话
  -> 固化命令、遥测、时钟和状态证据
  -> 生成并密封兼容 Run Bundle
  -> 执行现有 ValidationOutcome 和 Claim Guard
```

## 2. 产品边界

### 2.1 本轮支持

- 单机软实时运行，不承诺硬实时截止期。
- 人在回路的启动、暂停、继续、单步、倍速、停止和受控遥控。
- 一个交互会话由一个隔离进程独占一个 Basilisk 仿真实例。
- 以 `whole_spacecraft.unified_native.v1` 为首个正式交互式整星内核。
- 逐步接入 ADCS、EPS、Propulsion、Thermal、Payload、Comm/Data 六个分系统的遥控适配器。
- 使用现有 Capability、effect、参数和遥测合同，不在实时层复制物理模型。
- 使用 REST 完成控制面命令，使用 WebSocket 推送实时遥测和状态事件。
- 会话结束后产出可审计、可回放、可验证的证据包。

### 2.2 本轮不支持

- 硬实时、确定上界实时操作系统或飞控闭环资质声明。
- 真实硬件、半实物、硬件在环或飞行软件认证。
- CCSDS、PUS、SLE、CLTU、CADU 等正式空间链路协议；首轮只定义平台内部 JSON 合同。
- LLM 直接进入毫秒级控制闭环或未经人工确认自动执行遥控。
- 任意 Python、任意模块属性写入、未知 Capability/effect 或项目外文件访问。
- 跨节点热备、分布式一致时钟和多机高可用。
- 运行中通用内存快照。首轮崩溃恢复依赖初始 TaskSpec 加命令日志的确定性重放。
- 通过实时运行提高现有 fidelity 或 Claim 等级。

## 3. 兼容性原则

1. 现有 `run_compiled_task`、CLI、Agent、API、ExecutionQueueWorker 和一次性 Runner 保持原路径。
2. 不改变 TaskSpec 1.0.0 已有必填字段和语义；交互配置使用独立的 SessionSpec。
3. 不改变 `/runs` 及 `/runs/{run_id}/telemetry*` 的响应合同。
4. 新接口使用独立前缀 `/interactive-sessions`，避免和批处理 Run 混用。
5. 交互式执行只通过新的 `InteractiveKernelAdapter` 接入；没有适配器的 Capability 必须结构化拒绝，不能回退到伪实时批处理。
6. 会话运行目录与密封 Run Bundle 分离。活动会话可追加，密封 Run Bundle 仍不可变。
7. 所有新增依赖必须进入现有依赖治理；MVP 优先复用 FastAPI、Starlette、SQLite 和标准库，不引入外部 Broker。
8. 原有全量测试、279 例自然语言证据和发布门不得下降。

## 4. 总体架构

```text
Web Workbench / Operator Client
  |-- REST: lifecycle, speed, step, telecommand, query
  `-- WebSocket: telemetry, command ACK, session events
                 |
Interactive Control Plane (FastAPI)
  |-- Session Registry
  |-- Authorization / Rate Limit / Idempotency
  |-- Command and Event Journal (SQLite)
  `-- WebSocket Fan-out with bounded queues
                 |
Per-session IPC
                 |
Interactive Session Process
  |-- SoftRealtimeClock
  |-- Telecommand Scheduler
  |-- InteractiveKernelAdapter
  |-- Telemetry Sampler
  `-- Append-only Evidence Writer
                 |
Persistent Basilisk Simulation Instance
                 |
Session Finalizer
  -> Run Bundle
  -> ValidationOutcome
  -> ClaimReport
  -> Experience capture (受现有治理约束)
```

控制面不得直接持有 Basilisk 对象。每个会话进程是唯一模型所有者，所有控制通过有界 IPC 消息传递，从而隔离崩溃、避免跨线程修改模型状态，并保留未来远程会话 Worker 的扩展空间。

## 5. 会话合同

### 5.1 SessionSpec

SessionSpec 是 TaskSpec 的附加执行合同，不修改 Canonical TaskSpec：

```json
{
  "schema_version": "sat-sim.interactive-session.v1",
  "task_spec_sha256": "...",
  "kernel_capability_id": "whole_spacecraft.unified_native.v1",
  "clock": {
    "initial_speed": 1.0,
    "tick_s": 0.1,
    "max_duration_s": 3600.0
  },
  "telemetry_profile": "operator_default",
  "command_profile": "whole_spacecraft_operator",
  "limits": {
    "command_queue_size": 1000,
    "subscriber_queue_size": 256,
    "max_subscribers": 8
  }
}
```

### 5.2 状态机

```text
CREATED -> INITIALIZING -> READY -> RUNNING
                            |        |  ^
                            |        v  |
                            |      PAUSED
                            |        |
                            |        +-> STEPPING -> PAUSED
                            v
                         STOPPING -> COMPLETED
                                `-> FAILED / ABORTED
```

约束：

- `start` 仅允许 `READY -> RUNNING`。
- `pause` 仅允许 `RUNNING -> PAUSED`，在当前积分边界完成。
- `resume` 仅允许 `PAUSED -> RUNNING`。
- `step` 仅允许在 `PAUSED`，每次执行正整数个 tick，结束后仍为 `PAUSED`。
- `set-speed` 仅允许活动会话；速度必须来自批准区间。
- `stop` 必须幂等；终态不可重新运行。
- 非法状态转换返回结构化冲突，不改变状态。

### 5.3 速度语义

- `speed=1.0`：目标仿真时间与墙钟等速。
- `speed<1.0`：慢放。
- `speed>1.0`：倍速；只承诺尽力追赶，不允许跳过物理积分 tick。
- `speed=0` 不作为速度值，暂停必须使用显式 `pause`。
- MVP 批准速度集合：`0.25、0.5、1、2、5、10`。
- 当计算速度不足以达到目标倍速时，必须上报 `DEGRADED_REALTIME_FACTOR`，不得丢积分步冒充达标。

## 6. 软实时内核

### 6.1 内核接口

```text
InteractiveKernelAdapter
  prepare(task_spec, session_spec)
  initialize()
  current_sim_time_s()
  advance_to(target_sim_time_s)
  apply_command(command)
  sample_telemetry(stream_ids)
  health()
  finalize(reason)
```

`advance_to` 必须保持相同的 Basilisk 实例和模型状态。首个技术门必须验证在同一实例上重复执行“配置更晚停止时刻并继续 ExecuteSimulation”与一次性执行在容差内等价。如果 Basilisk 目标路径不支持安全续跑，该 Capability 不得进入交互模式，必须实现经审查的增量适配器，不能每 tick 重建模型。

### 6.2 调度循环

每个会话进程维护单线程确定性循环：

1. 读取到期且已校验的遥控指令。
2. 按 `(execute_at_sim_time_s, sequence, command_id)` 稳定排序。
3. 在积分边界应用指令。
4. 将内核推进到下一个 tick。
5. 采样到期遥测流。
6. 写入命令、状态、时钟和遥测日志。
7. 按目标速度进行墙钟节流或记录落后。

模型推进线程不得执行网络 I/O、数据库查询或慢客户端写操作。

## 7. 遥控指令合同

### 7.1 Telecommand

```json
{
  "schema_version": "sat-sim.telecommand.v1",
  "command_id": "tc_...",
  "session_id": "session_...",
  "sequence": 42,
  "target": "subsystem.adcs",
  "operation": "set_attitude_target",
  "parameters": {"pitch_deg": 10.0},
  "execute_at_sim_time_s": 125.0,
  "expires_at_sim_time_s": 130.0,
  "issued_by": "operator-id",
  "idempotency_key": "..."
}
```

### 7.2 命令状态

```text
RECEIVED -> ACCEPTED -> SCHEDULED -> EXECUTING -> SUCCEEDED
        `-> REJECTED             `-> FAILED
                    `-> EXPIRED / CANCELLED
```

每次转换产生不可变 ACK 事件，包含命令 ID、状态、仿真时间、墙钟时间、reason code 和证据哈希。

### 7.3 命令治理

- 每个 operation 必须在版本化 `InteractiveCommandContract` 注册。
- 合同声明目标、角色、参数 Schema、范围、单位、允许状态、执行适配器、遥测反馈和 Claim 边界。
- 只允许适配器调用公开模型控制入口或已注册消息，不允许 `setattr` 任意写入。
- 相同 idempotency key 和相同内容返回原 ACK；同 key 异内容 fail-closed。
- 过期、乱序、重复、越权、未知目标、未知 operation 和非法参数全部结构化拒绝。
- LLM 只能生成“遥控建议”；实际提交必须由具备 operator 权限的人确认。

### 7.4 首批命令族

- 会话控制：暂停、继续、单步、倍速、停止。
- ADCS：目标姿态/模式切换、动量卸载请求。
- EPS：负载开关、安全模式请求。
- Thermal：加热器自动/开/关命令，受温度安全合同约束。
- Payload：载荷开关、观测模式请求。
- Comm/Data：下行开关、请求数据率、队列清理策略。
- Propulsion：首轮只允许已注册、安全范围内的受控点火请求；必须有额外确认和互锁。
- 故障注入：仅测试角色可用，且只允许批准 effect。

## 8. 遥测合同

### 8.1 TelemetryFrame

```json
{
  "schema_version": "sat-sim.telemetry-frame.v1",
  "session_id": "session_...",
  "stream_id": "spacecraft_housekeeping",
  "sequence": 384,
  "sim_time_s": 125.1,
  "wall_time_utc": "...",
  "state": "RUNNING",
  "values": {"eps.battery_soc": 0.81},
  "quality": {"status": "VALID", "dropped_since_last": 0}
}
```

### 8.2 推送和回放

- WebSocket 推送实时 `TelemetryFrame`、`CommandAck` 和 `SessionEvent`。
- REST 提供当前快照、按 cursor 分页的历史和会话状态，不替代 WebSocket。
- 复用现有 `outputs.telemetry_streams` 的 stream ID、字段、单位和采样周期。
- 新增实时 profile 只能引用 Capability 已声明输出，不得暴露 simulator truth 作为普通可观测遥测。
- 每个订阅者使用有界队列。慢客户端采用 drop-oldest，并在下一帧报告丢帧数；不得反压仿真线程。
- 会话进程必须把完整原始遥测写入本地证据日志，客户端丢帧不等于证据丢失。

## 9. API 合同

新增接口，不改变现有 API：

```text
POST   /interactive-sessions
GET    /interactive-sessions
GET    /interactive-sessions/{session_id}
POST   /interactive-sessions/{session_id}/start
POST   /interactive-sessions/{session_id}/pause
POST   /interactive-sessions/{session_id}/resume
POST   /interactive-sessions/{session_id}/step
POST   /interactive-sessions/{session_id}/speed
POST   /interactive-sessions/{session_id}/stop
POST   /interactive-sessions/{session_id}/commands
GET    /interactive-sessions/{session_id}/commands/{command_id}
GET    /interactive-sessions/{session_id}/telemetry
WS     /interactive-sessions/{session_id}/live
```

创建会话只接受已严格验证的 TaskSpec 或 TaskSpec 哈希。自然语言必须先走现有 Agent 生成并验证 TaskSpec，再由人确认创建会话。

## 10. 人在回路界面

工作台新增独立的“交互仿真”视图：

- 会话状态、仿真时间、墙钟落后量和实际倍速。
- 启动、暂停、继续、单步、调速和停止控件。
- 遥控命令面板，按目标和 operation 展示合同生成的字段。
- 危险命令二次确认，显示影响对象、参数和互锁结果。
- 多速率遥测曲线、关键状态、命令 ACK 时间线和告警。
- 连接断开、重连、数据丢帧、会话故障和降速状态。

界面不能通过直接修改前端状态伪造仿真状态；所有显示以服务端序列化事件为准。

## 11. 持久化与证据

活动会话目录允许追加，至少保存：

```text
session_spec.json
task_spec.json
state_events.jsonl
clock_events.jsonl
telecommands.jsonl
command_acks.jsonl
telemetry/*.jsonl
health.jsonl
session_record.json
```

结束时：

1. 校验序列连续性、命令状态终结性和日志哈希链。
2. 导出兼容 ExecutionPlan 和运行摘要。
3. 生成标准 Run Bundle 扩展制品，不修改原有必需文件。
4. 运行现有物理验证和 Claim Guard。
5. 密封后禁止追加。
6. 只有密封且验证通过的会话才允许进入 Experience Store。

## 12. 安全要求

- 默认只监听 loopback；远程访问必须显式配置 TLS 终止和身份认证。
- viewer 只能查看，operator 可执行普通遥控，test_operator 可注入批准故障，admin 可终止和治理会话。
- 命令内容、ACK、身份、连接和状态转换写入审计链。
- 每会话、每身份限制命令速率、连接数、消息大小和队列深度。
- WebSocket 首次连接和重连都必须重新鉴权并绑定 session scope。
- 未知字段、NaN/Inf、超长字符串、路径、代码片段和 Schema 外参数 fail-closed。
- Propulsion、故障注入和安全模式解除属于高风险命令，需要显式确认 token，且 token 绑定命令哈希并短时有效。
- 断开浏览器连接不得自动停止仿真；控制租约超时后的策略必须可配置为继续、暂停或安全停止。

## 13. 可靠性要求

- 控制面重启后能从 SQLite 恢复会话目录和进程存活状态，不重复执行已 ACK 命令。
- 会话进程异常必须进入 `FAILED`，保留最后完整 tick 和未完成命令状态。
- 暂停期间仿真时间不得前进，遥测可继续发送低频心跳。
- 单步必须精确推进指定 tick 数，不能受墙钟节流影响。
- 重复使用相同 TaskSpec、种子和命令日志进行离线重放，语义状态和遥测在声明容差内一致。
- 客户端断开、慢消费和重连不能阻塞或终止仿真进程。
- 停止、超时和取消必须清理进程、IPC、锁和订阅者。

## 14. 初始性能目标

以下是冻结前的工程目标，必须由基准测试确认或调整：

| 指标 | 初始目标 |
| --- | ---: |
| 1x 模式持续运行 | 60 分钟无会话失败 |
| 基准 tick | 0.1 s |
| 普通遥控接收到 ACCEPTED ACK p95 | <= 100 ms |
| 到期命令执行误差 | <= 1 个仿真 tick |
| 10 Hz 遥测服务端发布延迟 p95 | <= 200 ms |
| 暂停响应 | <= 2 个 tick 且 <= 500 ms |
| 单步误差 | 0 tick |
| 1x 实际时间因子，60 秒窗口 | 0.95–1.05 |
| 同会话 WebSocket 客户端 | >= 5 |
| 默认客户端队列 | 有界且慢客户端不阻塞仿真 |
| 两小时资源增长 | 无持续无界增长 |

2x、5x、10x 是否达标取决于模型计算成本。不能达到目标时必须报告实际因子，不将尽力执行记为实时 PASS。

## 15. 验收定义

交互式仿真器只有同时满足以下条件才能宣称可用：

1. 同一常驻内核分段执行与一次性执行在容差内等价。
2. 状态机所有合法/非法转换均有自动化测试。
3. 暂停、继续、单步和六档倍速行为符合合同。
4. 至少六类代表遥控跨 ADCS、EPS、Thermal、Payload、Comm/Data、Propulsion 实际改变直接遥测。
5. 命令 ACK/NACK、幂等、过期、乱序、越权和互锁测试通过。
6. WebSocket 实时遥测、重连、慢客户端和丢帧计数测试通过。
7. 会话停止后 Run Bundle 密封、完整性和 ValidationOutcome 通过。
8. 固定 TaskSpec、种子和命令日志重放一致。
9. 中断、控制面重启、会话崩溃和清理测试通过。
10. 现有 641 项回归、平台验收、Doctor、pip check 和 pip-audit 继续 PASS。
11. 所有声明明确为软实时内部工程仿真，不代表硬实时或真实硬件验证。

