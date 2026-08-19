# 软实时交互式卫星仿真器验收清单

日期：2026-08-01  
状态：已完成（RT0 至 RT8 全部 PASS）  
关联规格：`realtime_simulator_spec.md`  
关联任务：`realtime_simulator_task.md`

## 1. 判定规则

- 状态只使用 `PASS`、`FAIL`、`BLOCKED`、`N/A`、`TBD`。
- 规划、接口定义或模拟返回值不等同于运行能力；必须有真实常驻会话、实际遥控执行和实时遥测证据。
- “实时”只允许表述为软实时，必须公布实测节拍、漂移、延迟和持续运行边界。
- 遥控支持必须同时具备命令合同、权限、校验、运行时交付、ACK/NACK 和效果遥测。
- 遥测支持必须具备字段合同、单调序号、仿真时间、数据质量、背压和缺口证据。
- 暂停、继续、单步和倍速必须改变同一个仿真实例，不得通过重跑整段仿真假装交互。
- 所有实时会话仍是内部工程仿真，不代表真实星上接口、硬实时、硬件在环或飞行验证。
- 任一新功能导致原批处理行为、API、CLI、脚本、Run Bundle 或冻结基线回归下降，发布门直接 FAIL。

## 2. 当前判定

| 目标 | 当前能力 | 判定 |
| --- | --- | --- |
| 常驻软实时会话 | 四个首批主 Capability 支持同一实例持久会话，并由 opt-in REST API 创建和管理 | PASS |
| 暂停、继续、单步、倍速 | 同一实例控制、状态事件和网络控制入口均已通过 | PASS |
| 模拟接收遥控 | REST TC 入口受权限、目录、确认和 ACK/NACK 约束 | PASS |
| 模拟发送遥测 | WebSocket 多流推送、补读、gap、背压和运行后归档已通过 | PASS |
| 不影响原功能 | 默认关闭时既有 OpenAPI 保持 103 路径；功能关闭和开启各 827/827 PASS | PASS |

## 3. 必须保持的原功能基线

| 基线 | 冻结值 | 扩展发布要求 |
| --- | ---: | --- |
| 对象三模式覆盖 | 24 部件、6 分系统、1 整星 | 不下降 |
| 自然语言接受矩阵 | 279/279 | 不下降 |
| 自然语言拒绝矩阵 | 10/10，false accept 0 | 不下降 |
| 全量 PyTest | 641/641，0 skipped | 不下降且新增测试全部 PASS |
| 严格 Doctor | 12/12 | PASS |
| 严格 release-check | 12/12 | PASS |
| Ubuntu 平台验收 | PASS | PASS |
| vLLM 代表案例 | 3/3，回退 0 | PASS |
| `pip check` | PASS | PASS |
| `pip-audit` | 0 漏洞 | 0 漏洞 |
| 批处理 Run Bundle | 密封和完整性 PASS | 语义不变 |

## 4. 功能堵塞项

| ID | 堵塞项 | 关闭标准 | 状态 |
| --- | --- | --- | --- |
| RT-F-01 | 无实时 Capability 支持声明 | 建立机器可读实时能力矩阵，区分可控、只读遥测和不支持 | PASS |
| RT-F-02 | 无持久仿真上下文 | 同一实例可分段推进，和一次性执行配对一致 | PASS |
| RT-F-03 | 无会话状态机 | CREATED 至终态全部合法/非法转换可验证 | PASS |
| RT-F-04 | 无软实时节拍器 | 支持 paced/unpaced、0.1x 至 10x、漂移观测和显式降速 | PASS |
| RT-F-05 | 无暂停/继续 | 暂停不推进 sim time，继续无回退、重复或状态重建 | PASS |
| RT-F-06 | 无单步 | PAUSED 下精确推进指定 quantum 并自动回到 PAUSED | PASS |
| RT-F-07 | 无运行时倍速切换 | quantum 边界安全切换，物理步不丢失 | PASS |
| RT-F-08 | 无遥控指令 Schema | TC 信封、命令目录、参数 Schema、单位和范围版本化 | PASS |
| RT-F-09 | 无指令邮箱和确定性调度 | 有界队列、仿真线程安全消费、同时间指令稳定排序 | PASS |
| RT-F-10 | 无 ACK/NACK 生命周期 | RECEIVED 到 ACKED/REJECTED/FAILED/EXPIRED 全链可查 | PASS |
| RT-F-11 | 无运行中动态控制映射 | 首批 4 个主 Capability 有受控命令适配器和效果证据 | PASS |
| RT-F-12 | 无实时遥测帧协议 | 帧包含 stream、sequence、sim time、wall time、quality 和值 | PASS |
| RT-F-13 | 无实时推送 | WebSocket 可订阅多 stream，实时收到实际仿真帧 | PASS |
| RT-F-14 | 无断线续读 | 支持 after_sequence；窗口外缺口显式报告 | PASS |
| RT-F-15 | 无交互工作台 | 会话控制、遥测曲线、指令表单和回执时间线可用 | PASS |
| RT-F-16 | 无实时会话封存 | 停止后生成兼容 Run Bundle、ValidationOutcome 和 ClaimReport | PASS |
| RT-F-17 | 无自然语言候选遥控 | 自然语言只生成候选，确定性校验和人工确认后执行 | PASS |

## 5. 性能堵塞项

| ID | 堵塞项 | 关闭标准 | 状态 |
| --- | --- | --- | --- |
| RT-P-01 | 分段推进开销未知 | 建立 quantum 0.1/0.5/1.0 s 的计算余量和抖动基线 | PASS |
| RT-P-02 | 墙钟漂移未知 | 单会话 1x、30 分钟 p95 绝对漂移不高于 200 ms | PASS |
| RT-P-03 | 指令延迟未知 | 接收到入队 p95 <=100 ms，效果 <=2 quantum 可见 | PASS |
| RT-P-04 | 遥测延迟未知 | 本地 10 Hz 交付 p95 <=200 ms，静默丢帧 0 | PASS |
| RT-P-05 | 慢客户端影响未知 | 背压测试中仿真线程不阻塞，gap/drop 可观测 | PASS |
| RT-P-06 | 倍速能力未知 | 0.1x/0.25x/0.5x/1x/2x/5x/10x 逐档记录实际可达率 | PASS |
| RT-P-07 | 多会话容量未知 | 正式门在单机并发 4 下冻结 CPU、内存、漂移和吞吐边界 | PASS |
| RT-P-08 | 长稳资源增长未知 | 单会话 4 小时无非预期线程、句柄和内存持续增长 | PASS |

## 6. 安全堵塞项

| ID | 堵塞项 | 关闭标准 | 状态 |
| --- | --- | --- | --- |
| RT-S-01 | 实时权限模型未定义 | viewer/operator/fault_operator/admin 权限和审计完成 | PASS |
| RT-S-02 | 任意命令注入风险 | 未注册 target/operation、任意代码、路径和越界参数 fail-closed | PASS |
| RT-S-03 | WebSocket 滥用风险 | 认证、来源、心跳、空闲超时、消息大小和速率限制通过 | PASS |
| RT-S-04 | 重放和重复执行风险 | command_id 幂等、哈希冲突拒绝、session revision 校验通过 | PASS |
| RT-S-05 | 故障注入越权风险 | 普通 operator 不能注入 fault/degradation，越权写审计 | PASS |
| RT-S-06 | LLM 直接控制风险 | LLM 候选不可绕过 Validator、确认门和命令目录 | PASS |
| RT-S-07 | 会话工作区篡改风险 | 指令/状态日志哈希链，篡改后封存和恢复均 fail-closed | PASS |
| RT-S-08 | 敏感信息进入遥测 | 字段白名单和脱敏测试通过，未知内部属性不可读取 | PASS |

## 7. 可靠性堵塞项

| ID | 堵塞项 | 关闭标准 | 状态 |
| --- | --- | --- | --- |
| RT-R-01 | 分段与批处理一致性未知 | 首批 Capability 的终态、遥测和事件配对均在容差内 | PASS |
| RT-R-02 | 状态转换竞态未知 | 并发 pause/resume/step/stop/command 无死锁和非法终态 | PASS |
| RT-R-03 | 客户端断线行为未知 | 断线不停止会话，重连状态和遥测序号正确 | PASS |
| RT-R-04 | API/Worker 中断恢复未知 | 未密封会话标记 INTERRUPTED，可验证重放或结构化失败 | PASS |
| RT-R-05 | 指令重放确定性未知 | 同 TaskSpec、种子和 ACK 日志产生相同语义哈希 | PASS |
| RT-R-06 | 队列满和过期行为未知 | 无静默丢命令，NACK/EXPIRED 原因稳定且可审计 | PASS |
| RT-R-07 | 资源释放未知 | 所有终态释放线程、句柄、端口、锁和 Basilisk 上下文 | PASS |
| RT-R-08 | 证据关联缺失 | 每条 ACK 可定位到命令效果遥测窗口和最终验证结果 | PASS |
| RT-R-09 | 原功能非回归未证明 | 现有基线和新实时矩阵全部 PASS | PASS |

## 8. 阶段门

| Gate | 关闭条件 | 当前 |
| --- | --- | --- |
| RT0_SCOPE_COMPATIBILITY | 首批 Capability、命令/遥测清单、非目标、性能预算和兼容边界冻结 | PASS |
| RT1_SEGMENTED_RUNTIME | 同一 Basilisk 实例分段推进通过批处理等价性、暂停和单步原型 | PASS |
| RT2_SESSION_CORE | 会话状态机、节拍器、持久工作区和资源隔离完成 | PASS |
| RT3_TELECOMMAND | 指令合同、邮箱、调度、权限、ACK/NACK 和首批命令适配器完成 | PASS |
| RT4_TELEMETRY | 增量遥测、WebSocket、背压、重连和运行后归档完成 | PASS |
| RT5_HUMAN_IN_LOOP | 工作台控制、命令确认、实时图表和故障注入权限完成 | PASS |
| RT6_EVIDENCE_RECOVERY | 会话日志、重放恢复、ValidationOutcome、ClaimReport 和 Run Bundle 封存完成 | PASS |
| RT7_PERFORMANCE_SECURITY | 延迟、漂移、容量、长稳、攻击和故障恢复矩阵通过 | PASS |
| RT8_FINAL_ACCEPTANCE | 新旧功能全量非回归和发布验收全部通过 | PASS |

## 9. 最低验收场景

### 9.1 会话控制

1. 从 canonical TaskSpec 创建 READY 会话并启动。
2. 运行中暂停，观察至少 2 秒墙钟内 sim time 不增长。
3. 单步 1、5、10 个 quantum，结果精确且回到 PAUSED。
4. 继续运行，无时间重复和遥测序号回退。
5. 依次切换 0.5x、1x、2x、5x，记录实际倍速和漂移。
6. 正常停止并封存；强制终止保留未密封和失败证据。

### 9.2 遥控

1. 正常 ADCS 目标调整并看到姿态误差响应。
2. EPS 负载指令并看到功率与 SOC 响应。
3. Comm/Data 数据生成或下行控制并看到队列/存储变化。
4. 经授权注入一个批准故障并看到直接效果。
5. 重复 command_id、冲突 command_id、过期、乱序、越权、未知命令和队列满均产生正确 NACK/EXPIRED。
6. 同一时间的多指令重复运行顺序一致。

### 9.3 遥测

1. 同时订阅快速流和 housekeeping 流，频率符合合同。
2. 断开后使用 after_sequence 补读，无重复；窗口外返回 gap。
3. 慢消费者触发有记录的 drop 或断开，不阻塞仿真。
4. 所有帧 sim time 单调，字段白名单和单位正确。
5. 运行后 REST 查询与实时日志在冻结规则下对齐。

### 9.4 恢复和证据

1. 会话进程在已 ACK 指令后崩溃，重启识别为 INTERRUPTED。
2. 从初始 TaskSpec 和命令日志重放，语义结果一致后恢复。
3. 篡改指令日志、状态日志或遥测日志，恢复和封存均拒绝。
4. 每条 ACK 指令在最终报告中具有效果窗口。
5. 完成会话通过现有 Run Bundle 完整性检查和物理 ValidationOutcome。

## 10. 最终停止条件

只有同时满足以下条件，扩展功能才可记为 PASS：

1. `RT0` 至 `RT8` 全部 PASS，四维清单无 BLOCKED。
2. 首批 4 个主 Capability 都完成实际实时会话、遥控、遥测和效果证据。
3. 暂停、继续、单步、倍速、重连、背压和恢复均有自动化证据。
4. 性能结果满足冻结预算，任何丢帧、降速和 gap 都显式可见。
5. 所有指令受注册合同、权限和审计约束，任意代码路径为 0。
6. 完成会话可密封并通过 ValidationOutcome 和 Claim Guard。
7. 原有批处理、API、CLI、Agent、经验系统和发布门禁不下降。
8. 文档明确软实时和工程仿真边界，不作硬实时、硬件或飞行声明。
