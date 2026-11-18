# P9-N4-B 预检合同开发经验 — 2026-11-18

## 本轮改动

- 新增 `tests/test_p9n_n4_preflight.py`，围绕已经注册的 Basilisk 原生反作用轮 TaskSpec 建立拒绝性测试。
- 覆盖原生后端不支持的故障、退化、时变力矩配置，以及采样周期和运行时长上限。
- 通过公开 `POST /tasks/validate` 检查错误是否以字段路径、错误码和可读消息返回。

## 为什么这样做

N4-B 的目标不是再造一个预检服务，而是保证用户配置经过现有 Canonical TaskSpec、Capability Adapter 和 Validator 后，能得到可操作的拒绝原因；不允许界面隐藏控件成为唯一安全边界。

## 本轮工程经验

1. **前端禁用不等于后端安全。** 用户仍可能通过旧草稿、导入文件或 API 提交不支持的 native 配置，因此必须从公开验证 API 覆盖拒绝行为。
2. **验证结果必须保留字段定位。** 只断言 `ok=false` 不够；测试同时检查 `native_scope` 错误码、字段路径和解释文本。
3. **不要把模型边界测试写成正向成功断言。** 对 native-only 能力，故障/退化/时变输入应明确失败关闭，不得静默回退到 project_reference。
4. **工作流路径过滤必须同步新增测试路径。** 新增测试文件后若未加入对应 Actions `paths`，PR 可能没有运行预期物理/集成门禁；本轮将新测试路径纳入 native-RW workflow。

## 验收状态

测试已提交到 PR #177。以该 PR 最新 head 的 Actions 结果为准；在 Actions 完成前不宣称通过。

## 追加复核（2026-11-18）

首次专项 CI 通过后，检查 Job Steps 发现预检测试文件虽然已加入 workflow 的路径过滤，但未加入 pytest 命令，属于真实的覆盖缺口。已补充独立步骤 `Verify P9-N4 native preflight rejection contract`，并用新提交重新触发 workflow。今后新增测试必须同时核对：文件存在、路径过滤包含、执行命令包含、最终日志出现明确 collected/passed 结果；仅检查 workflow 总体绿色不足以证明新测试被执行。


## 失败复盘与修正（2026-11-18）

第一次执行新测试后，发现两类测试设计问题：

- 通用 `POST /tasks/validate` 只覆盖 Canonical TaskSpec Schema/语义/Agent Guard，不会完整执行注册 Capability Planner 的 backend 专项约束。测试若要证明 native adapter fail-closed，必须走产品实际使用的 `POST /tasks/parse`（`input_kind=task_spec`、`compile_if_valid=true`）并检查 `result.validation`。
- 将退化事件放入前端事件草稿结构会先被参数路径治理拒绝，无法证明 native adapter 自身拒绝。应使用 capability 已声明的物理参数路径（本例为 `degradations.adcs.reaction_wheel.friction_increase_pct`），隔离验证 native backend 的专属约束。

修正原则：测试必须命中它声称验证的那一层。通用 Schema 验证、Capability Adapter 验证、Planner 编译和最终执行是不同边界，不能用其中一层的拒绝结果替代另一层的证据。
