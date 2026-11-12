# P7-A 第一批：任务历史对话框（2026-11-12）

## 目标和边界

以 [对话工作台路线图](2026-11-11-conversational-simulation-product-roadmap.md) 的 P7-A 为依据，让用户在“任务中心 → 任务详情”对话框内完成两项高频操作：解释失败或验收异常的运行记录；选择两个历史 Run 并比较真实指标和遥测。保留现有任务版本、TaskSpec 和 Run Bundle 契约。

本批不实现 P7-B 自然语言转图形、不进行批量删除或 Run 重算。任务搜索和状态筛选已有实现，先针对同一任务内的运行历史提供状态过滤。跨中心的全面上下文复用与窄屏/键盘全旅程验收在 P7-C/D 继续推进。

## 实施任务（TDD）

### 1. 回归约束：先补失败和对比测试

- 在 `tests/test_task_center.py` 添加静态入口契约：任务详情对话框存在历史过滤、对比操作、证据说明和对比承载区，脚本包含 Run Bundle 预检和明确的状态/验证区分。先确认测试因缺少入口失败。
- 添加 `tests/js/task_history_vm.js`：通过抽取 `app.js` 原始函数，在 Node VM 中验证失败阶段解释（基于 `execution_state`/`run_record`/`validation_outcome`）、候选 Run 预检（密封、成功、同模型）和失败后的无执行副作用。
- 追加 Chromium 任务详情交互测试：筛选、两运行选择、对比展示与后端只读接口调用；由于本容器浏览器沙箱限制，以 CI 真 Chromium 结果作最终浏览器验收。

### 2. 对话框内的运行历史

- 在 `index.html` 任务详情中添加状态筛选、已选摘要、对比按钮、原因说明区和对比结果容器。
- `app.js` 保存当前任务历史与两 Run 选择；筛选不改变后端历史记录和任务版本。
- 运行异常“查看原因”按需读取 `/runs/{id}/execution` 与 `/runs/{id}`，只呈现记录可证实的阶段、状态与原因码；没有明确原因时说明证据不足而非推断根因。网络失败显示明确错误。

### 3. 复用运行中心的比较能力

- 对比前从 `/runs/{id}` 分别读取两个 Run 的封存和能力标识，并验证成功、密封且同模型；不接受只凭 Task Center 列表的成功标志。
- 将现有 `#runComparePanel` 暂时挂载到任务对话框，复用 `compareSelectedRuns()` 及 `/runs/compare`、metrics/plots/telemetry 分页展示；关闭详情恢复原位置并使过期异步响应失效。
- 不增设执行、回放、取消、任务覆盖或独立比较契约。

### 4. 验收和交付

- 本地运行 JS 语法、Node VM、非浏览器 pytest、`git diff --check`。
- 远程 PR 运行 GitHub Actions 的 Chromium、单次运行 Creator、Agent 契约、Basilisk 原生回归；失败追根修复，不以 skip 当成功。
- P7-A 第一批独立 Draft PR；全部门禁通过后再评审 Ready/合并。P7 总体验收需 P7-B/C/D 后单独完成。
