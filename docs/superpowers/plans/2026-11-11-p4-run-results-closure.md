# P4：运行过程与结果闭环

## 目标

将单次有限仿真的提交、执行状态、结果读取与后续操作收敛到对话式工作区。复用现有 Run API、Run Bundle、遥测、事件、plot manifest、制品与控制器；不创建第二套运行状态机，不复制结果数据，也不以 UI 动画代替真实执行状态。

## 当前代码结构核查

- `runSimulation()` 已提交真实 Run，并使用返回的 `run_id` 跟踪执行。
- `pollRunExecution(runId)` 已通过 `/runs/{run_id}/execution` 获取真实执行状态；终态后调用 `loadRun()`。
- `loadRun()` 已读取 Run、metrics、events、plots、telemetry、artifacts、dataset、FMEA 与 fault traceability。
- 结果渲染依赖一组唯一 DOM ID（`activeRunCard`、`metricGrid`、`telemetryChart`、`resultEventList`、`artifactList` 等），且现有事件监听器直接绑定这些节点。
- 当前 `wizardRunResultsHost` 是空容器；提交完成后虽然切换到 `results` 步骤，但现有结果 DOM 仍位于 `creatorRunPanel`，而普通向导视图与 Creator 视图分别受视图切换控制。因此，当前存在“对话已进入结果步骤，但真实结果面板仍在旧工作区”的集成缺口。

## 实施方案

### P4-A：结果面板单实例挂载

- 将现有 `creatorRunPanel` 作为唯一结果面板，按当前工作区在原位置与 `wizardRunResultsHost` 之间移动。
- 保存原始父节点和相邻节点，进入普通向导结果步骤时挂载到向导容器；返回高级工作台时恢复原位置。
- 不 clone 结果 DOM，不生成重复 ID，不重新绑定一套事件，不改变任何结果 API。
- 实时会话工作区仍使用自己的既有控制台，不把有限运行结果面板错误挂载到实时会话。

### P4-B：对话式运行状态

- 复用真实 `execution_state` 与 `activeRunId` 展示排队、运行、取消请求、成功、失败、超时和取消状态。
- 进度条只在服务端提供真实进度时显示对应数值；没有数值时展示明确的“正在执行/等待 Worker”，不估算百分比。
- 显示任务名称、Run ID、提交时间、状态与服务端错误信息。
- 取消操作继续调用现有安全检查点取消 API；不因离开页面自动取消任务。

### P4-C：结果分区与缺失状态

- 概览：沿用真实 metrics、validation 与运行元数据。
- 曲线：沿用 plot manifest 和 telemetry，保留时间范围、曲线选择与覆盖率说明。
- 事件：沿用事件 API 的原始记录与时间戳。
- FMEA/证据：沿用现有 traceability/FMEA 数据与验证状态。
- 制品：沿用 artifacts API 返回的文件与下载入口。
- 每个分区区分“加载中、已有数据、无数据、读取失败”；接口失败不能静默伪装成空结果。
- 运行失败、部分输出和缺失制品必须保留真实状态与诊断信息。

### P4-D：结果后的连续操作

- 提供重新运行、复用当前配置、查看报告、下载诊断包、导出数据集和查看历史运行入口；按钮仅在现有服务端能力及数据条件满足时显示。
- 历史 Run 选择后仍由 `loadRun(runId)` 加载；请求序号保护继续防止旧请求覆盖用户新选中的 Run。
- 从结果返回配置时保留当前配置；明确再次运行会创建新的 Run，不覆盖历史记录。

## 测试与验收

1. 静态契约：结果面板只有一个实例；portal 恢复锚点；不存在重复 ID。
2. Chromium：向导运行完成后结果面板真实出现在向导中；切换高级工作台后面板恢复；再次切回不丢状态。
3. 状态：QUEUED/RUNNING/CANCEL_REQUESTED/SUCCEEDED/FAILED/CANCELLED/TIMED_OUT 分支均显示正确；取消仍经过现有 API。
4. 结果：metrics、plot manifest、telemetry、events、traceability、artifacts 均由真实 API 数据驱动；缺失和失败状态可区分。
5. 回归：现有单次 Basilisk、Agent 输入/运行一致性、实时控制台和会话生命周期测试保持通过。

## 不在本阶段范围

- 不修改 Basilisk 物理模型、Capability Registry、TaskSpec、Run Bundle schema 或结果 API。
- 不把持续实时会话强行改成有限运行结果页。
- 不引入模拟进度、合成指标、虚构遥测或默认成功状态。
- 不合并主干；P4 完成后仍需按发布门槛审查依赖 PR 与完整回归。
