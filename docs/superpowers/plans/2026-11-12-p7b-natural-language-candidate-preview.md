# P7-B 第一批：自然语言候选预览与显式应用（2026-11-12）

## 目标
自然语言解析先生成候选 TaskSpec，在对话框内展示字段差异和标准图形流程骨架；用户明确确认后才投影到原生表单，或应用后进入原生图形画布。

## 边界
- 复用现有 `POST /tasks/parse`、TaskSpec 规范化/校验和无损表单投影。
- 候选生成阶段不修改当前 `state.taskSpec`、`state.formData`、当前 Capability 或图形编辑器状态。
- 图形骨架仅为标准工作流预览，不宣称服务端 `compile-graph` 已通过。
- 用户确认应用后才调用现有 `projectTaskSpecToForm`；图形正式校验仍需用户在原生画布显式触发。
- 放弃候选不改变当前配置；不自动运行、不执行任意 Python。

## 实施
向导入口生成候选；展示字段差异与图形骨架；提供确认应用、应用并打开图形画布、放弃三个动作；保留其他 Agent 入口原行为。

## 验收
静态契约、Node VM、真实 Chromium 交互、Agent/TaskSpec/Graph/Basilisk 回归。
