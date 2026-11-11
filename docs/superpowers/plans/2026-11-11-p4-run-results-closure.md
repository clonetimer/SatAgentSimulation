# P4：运行过程与结果闭环

## 目标

让普通用户在对话工作区完成单次有限仿真的提交、状态跟踪、结果查看和后续操作。复用现有 Run API、Run Bundle、遥测、事件、plot manifest、制品与控制器；不创建第二套运行状态机，不复制结果数据，也不以 UI 动画代替真实执行状态。

## 代码审计结论

P4 的主要数据链和 UI 面板其实已经存在，本轮核查确认：

- `runSimulation()` 提交真实 Run，并使用服务端返回的 `run_id` 跟踪执行。
- `pollRunExecution(runId)` 通过 `/runs/{run_id}/execution` 读取真实状态；终态后调用 `loadRun()`。
- `loadRun()` 读取 Run、metrics、events、plots、telemetry、artifacts、dataset、FMEA 与 fault traceability。
- `renderExecutionProgress()` 更新现有运行状态卡；取消操作调用既有安全检查点取消 API。
- `wizardAttachPanels()` 已将唯一的 `creatorRunPanel` 移入 `wizardRunResultsHost`；`wizardRestoreAdvancedPanels()` 在返回高级工作台时将其恢复到 `creatorView`。
- 结果面板包含概览、曲线、事件、FMEA/证据、制品、最近运行，以及复用配置、报告、诊断包和数据集导出等既有入口。

因此，不应另建结果组件或重复实现 API。P4 的当前工作重点是冻结上述单实例集成契约、补齐对话工作区的运行/结果闭环回归，并核查边界状态和缺失数据展示。

## 本轮已完成

### P4-A：唯一结果面板挂载/恢复（现有实现审计通过）

- 确认 `creatorRunPanel` 只存在一个 DOM 实例。
- 向导入口复用 `wizardAttachPanels()`，高级工作台入口复用 `wizardRestoreAdvancedPanels()`。
- 新增 `tests/test_p4_conversational_run_results.py`，冻结唯一面板、挂载/恢复路径及关键结果容器 ID 不重复的契约。

### P4-B：真实运行状态（现有链路审计通过）

- 真实状态来自 `execution_state`，覆盖 QUEUED、RUNNING、CANCEL_REQUESTED 及终态。
- 没有服务端进度百分比时不应推测百分比；只展示真实状态和队列/执行说明。
- 取消仍走原安全检查点接口；离开工作区不自动停止运行。

### P4-C：结果分区（现有链路审计通过）

- 概览、曲线、事件、FMEA/证据和制品均复用既有结果 DOM 和数据源。
- `loadRun()` 的请求序号保护避免较早请求覆盖用户后来选择的 Run。
- 新增静态契约测试，确认结果分区仍使用现有 API 和唯一 DOM 节点。
- 修复结果子接口异常被静默降级为空数据的问题：现在逐项保留真实读取错误，并在运行概览显式列出失败的数据源；遥测概览不可用时继续明确标注是否截断，避免误读为全程数据。

### P4-D：结果后的连续操作（既有入口审计通过）

- 复用配置、查看报告、下载诊断包、导出数据集和最近运行选择继续沿用原控制器。
- 再次运行创建新的 Run，不覆盖历史记录。

## P4 验收结论

<badge>已完成（工作台集成范围）</badge>

- 最新分支回归已通过：Continuous simulation browser smoke、Single-run creator UX + real Basilisk finite-run、Agent input/run consistency、unit tests、static checks 和 script/dependency governance。
- 新增 P4 静态契约测试已纳入单元测试回归。
- 原生 Basilisk 工作流中已完成的整星回归步骤、C++ 电池契约和 Windows Job Object 生命周期通过；3600 秒长时 soak 在本次核查时仍处于执行中，因此不把它写成已通过。
- 结果接口的部分失败现在有明确数据源错误提示；运行主记录读取失败仍显示真实错误，不会显示成功结果。
- 实时会话仍由独立控制台和原有控制器管理；本阶段没有将实时会话混入有限运行结果面板。

本阶段完成的是对话工作台与现有运行/结果链路的集成，不代表全部结果接口故障组合均已完成独立端到端测试，也不代表物理模型或飞行资格验收。

## 不在本阶段范围

- 不修改 Basilisk 物理模型、Capability Registry、TaskSpec、Run Bundle schema 或结果 API。
- 不把持续实时会话强行改成有限运行结果页。
- 不引入模拟进度、合成指标、虚构遥测或默认成功状态。
- 不合并主干；P4 完成后仍需按发布门槛审查依赖 PR 与完整回归。
