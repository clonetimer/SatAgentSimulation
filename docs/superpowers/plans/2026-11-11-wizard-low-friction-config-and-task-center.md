# 第三阶段向导 UX 实施计划

- [x] 检查用户截图和现有 Wizard/Task Center/trace option 实现。
- [x] 确定渐进式参数、元数据驱动曲线分组和任务管理向导方案。
- [x] 参数解释、推荐值和快捷预设；高级字段继续折叠。
- [x] 曲线按已注册分系统元数据分组，一键添加/移除；原始技术字段选择降为高级入口。
- [x] 任务中心列表、搜索、状态筛选、详情/版本/运行历史、克隆/编辑/运行/删除集成向导。
- [x] 向导任务编辑和运行留在同一 Wizard Shell，复用原 API 和 Run Bundle。
- [x] 增加 pytest 契约与 Chromium 测试。
- [x] 将自然语言解析器和 TaskSpec 编辑器以原 DOM/控制器方式挂入向导；保留旧高级工作台入口。
- [x] 为批量实验、目标实验、图形组装和系统诊断保留独立专业工作区，并从向导提供入口；文档说明不适合强行聊天化的原因。
- [x] 增加自然语言/TaskSpec/专业工具的契约测试。
- [x] node --check、Wizard pytest 契约测试通过。
- [x] Chromium 单次工作台/向导流程通过；首次回归发现首页选项数从 5 增至 7，已同步修正浏览器断言并重新触发 CI。
- [x] 真实 Basilisk 有限时长单次/分段仿真回归通过。
- [x] Chromium 连续控制台回归通过。
- [x] Linux 官方 wheel clean bootstrap 通过。
- [ ] Windows 官方 wheel clean bootstrap、Native whole-spacecraft 深度测试及 Agent 输入/运行一致性回归仍在运行；完成后复核再考虑 Ready。
