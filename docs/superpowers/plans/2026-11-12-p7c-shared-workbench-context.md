# P7-C 第一批：跨专业工具共享上下文（2026-11-12）

## 目标
在实验中心、目标实验、运行中心和系统诊断之间切换时，持续显示当前任务、任务版本、Run ID、Run Bundle、实验和目标引用，并能返回进入专业工具前的来源工作区。

## 边界
- 复用 activeTaskCenterId、activeRunId、activeGoalId 与 TaskSpec；sharedWorkbenchContext 只保存关联标识和来源，不复制 TaskSpec 或 Run Bundle 内容。
- Task Center / Run Bundle API 仍是权威数据源；不修改后端数据契约。
- 首次进入专业工具记录来源；专业工具之间切换不覆盖来源；返回后清除来源引用。
- 实时会话离开保护保持原样，不自动停止、恢复会话或重放遥控指令。

## 功能
1. 四个专业工作区显示同一上下文条。
2. 打开任务记录 Task ID / version；打开运行记录 Run ID / Bundle 引用。
3. 查看实验对比、目标详情时记录 Experiment ID / Goal ID。
4. 提供返回来源工作区动作，并保持任务/运行引用。

## 验收
静态契约、Node VM 状态转换、真实 Chromium 连续切换、Agent/TaskSpec/Basilisk 回归。
