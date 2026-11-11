# 规则驱动式仿真向导实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox ([ ]) syntax for tracking.

**Goal:** 默认向普通用户提供不依赖 LLM 的规则驱动仿真向导，同时保留完整高级工作台。
**Architecture:** 在现有单页应用中增加独立 wizardView 和前端状态机；所有模型选择、事件添加、校验、运行和结果读取复用现有函数及 API。向导使用 Presentation Catalog 和当前 Schema 动态生成选项，不复制物理/执行逻辑。
**Tech Stack:** FastAPI 静态资源、原生 JavaScript、HTML/CSS、pytest/TestClient。
**Spec:** docs/superpowers/specs/2026-11-11-rule-driven-simulation-wizard-design.md

## Global Constraints
- Basilisk 版本和物理载荷不变；不修改物理模型或物理参数语义。
- 不引入 LLM、Prompt、Agent 调用或新运行后端。
- TaskSpec、Capability Registry、Durable Queue、Run Bundle 契约保持不变。
- 高级工作台及其图形组装、Agent、TaskSpec、实验和实时功能完整保留。
- 运行前必须经过现有 previewForm；校验失败不能调用 runSimulation。

## Review Focus
- 对象缺少可执行配置能力：向导隐藏或禁用，不得尝试运行。
- 对象没有注册故障：明确显示无可用故障，不得硬编码故障。
- 用户返回或重置流程：不意外清除当前工作台已编辑配置。
- 预览失败或未校验时：运行入口保持禁用。
- 小屏/窄屏：选项卡片单列，页面无横向溢出。

---

### Task 1: 设计文档与向导契约测试
**Files:** create design doc, plan doc, tests/test_workbench_wizard.py.
- [ ] 写测试：默认入口为 wizard、五个预定义目标、保留高级工作台入口、向导 DOM 与状态机存在。
- [ ] 运行 pytest -q tests/test_workbench_wizard.py，确认按预期因新向导不存在而失败。
- [ ] 保存设计与实施计划。
- [ ] 提交文档和失败测试。

### Task 2: 向导默认入口与布局
**Files:** modify src/sat_sim/web/index.html, compact-workbench.css, app.js.
**Interfaces:** 新增视图 ID wizardView、导航值 wizard；状态 wizard 包含 goal、step、level、objectId、faultEffect、validated。新增 renderSimulationWizard、wizardStart、wizardChooseLevel、wizardChooseObject、wizardBack、wizardReset。switchView('creator') 仍指向现有高级工作台。
- [ ] 增加默认首页和高级工作台两个入口的结构测试并确认失败。
- [ ] 新增独立 wizardView、单列对话布局和响应式样式；不删除 creatorView。
- [ ] 将默认视图设为 wizard，更新 switchView 映射及 active nav；初始化完成后渲染向导。
- [ ] 运行向导契约测试和现有静态 Workbench 测试。

### Task 3: Registry 驱动的规则状态机
**Files:** modify src/sat_sim/web/app.js; test tests/test_workbench_wizard.py.
**Interfaces:** 目标 normal_run、fault_run、history、compare、advanced；对象层级 whole_spacecraft、subsystem、component。对象只读自 state.presentationObjects；仅允许有 primary_capability_id 或 configuration_capability_id 的对象。故障只读自 effectCatalog('fault')。选择对象调用 selectCatalogObject(objectId)。
- [ ] 添加目标分流、层级过滤、可执行对象过滤和无故障目录空状态测试。
- [ ] 确认测试失败。
- [ ] 实现逐步消息与按钮选项；正常/故障路径进入对象选择，历史/比较/高级路径跳转现有视图。
- [ ] 选择对象后调用现有选择函数；故障路径用现有事件模板和 addEffect 添加，不构造新故障结构。
- [ ] 运行向导测试及对象目录/事件目录回归。

### Task 4: 配置、预览和运行闭环
**Files:** modify src/sat_sim/web/app.js; test tests/test_workbench_wizard.py.
- [ ] 添加运行时长/采样间隔映射、默认值、校验门禁和运行跳转测试。
- [ ] 确认测试失败。
- [ ] 用 Schema 中 simulation.duration_s、simulation.sample_s 字段约束渲染数字输入，更新 state.formData，再调用 renderForm、renderEvents、renderOutputs 和摘要刷新。
- [ ] 配置摘要页调用现有 previewForm；失败时停留并显示错误，成功后才启用运行按钮。
- [ ] 运行按钮调用既有 runSimulation，之后进入 creator 结果面板；不直接构造 API 请求。
- [ ] 运行向导专项测试、原有任务解析/执行计划测试。

### Task 5: 文档、全量验证与 PR 收敛
**Files:** modify docs/guides_zh/02_工作台使用指南.md and README.md; run existing Workbench, Browser, Basilisk finite-run regression.
- [ ] 更新使用指南，说明普通向导和高级工作台边界。
- [ ] 运行 pytest -q tests/test_workbench_wizard.py tests/test_web_workbench.py。
- [ ] 运行现有 Browser/Chromium 和真实 Basilisk 有限运行工作流；不将未执行的检查标为 PASS。
- [ ] 检查 Git diff、资源版本缓存、无 LLM 调用和所有导航路由。
- [ ] 更新 PR #113 验证说明；仅在 CI 和真实回归满足要求后转 Ready，不自动合并 main。