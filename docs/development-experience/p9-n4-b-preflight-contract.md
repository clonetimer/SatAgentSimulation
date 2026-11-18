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