# P6 第一批正式验收记录

**项目：** `clonetimer/SatAgentSimulation`  
**验收日期：** 2026-11-12  
**功能基线：** `ee6663e7f0e7272456646eec375db3c1292a1059`  
**P6 PR：** [#114 Conversational shell for guided and realtime simulation](https://github.com/clonetimer/SatAgentSimulation/pull/114)  
**前置基线 PR：** [#113 Rule-driven simulation wizard](https://github.com/clonetimer/SatAgentSimulation/pull/113)

## 1. 验收范围

P6 第一批验收目标是把对话式仿真工作台与原有任务中心、TaskSpec、拖拽式图形建模和专业工具工作区接通。对话式界面只承担入口、引导和上下文呈现；真实业务状态仍由既有表单、Capability Registry、TaskSpec、后端 API、Run Bundle 和 Basilisk 执行链维护。

### P6-A 图形建模与对话引导
- 原生图形画布增加建模助手，按当前图状态给出下一步提示。
- 恢复推荐流程、自动布线、正式校验继续复用原控制器；覆盖性动作有确认。
- 保留模块拖拽、节点移动、类型化端口、撤销/重做、多模块装配及工程导入导出。
- 服务端 `compile-graph` 仍是正式校验权威。

### P6-B 任务中心
- 常用操作为任务详情、编辑配置和运行任务；克隆/删除渐进披露。
- 保留不可变版本、克隆独立任务、运行历史与 Run Bundle 语义。
- 浏览器回归验证任务列表、克隆、版本详情和专业工具往返。

### P6-C TaskSpec / 自然语言与图形建模
- TaskSpec 与自然语言投影后的配置均可进入同一原生图形画布。
- 同一 Capability 保留图形状态；需要替换模型/画布时要求用户确认。
- 不自动生成代码或运行，正式执行前仍需服务端校验。

### P6-D 专业工具入口与返回
- 向导提供运行中心入口。
- 实验中心、目标实验、系统诊断和运行中心提供统一返回向导路径。
- 返回后原向导步骤、配置、任务版本和运行记录仍可访问。

## 2. CI 验收结果

功能基线 `ee6663e7f0e7272456646eec375db3c1292a1059` 对应七类工作流全部通过。

| 工作流 | 结果 | 证据 |
|---|---|---|
| Continuous satellite static checks | PASS | [43156737075](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737075) |
| Project script and dependency governance | PASS | [43156737038](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737038) |
| Continuous satellite unit tests (without Basilisk) | PASS | [43156737011](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737011) |
| Continuous simulation browser smoke | PASS | [43156737039](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737039) |
| Agent input and run consistency | PASS | [43156737024](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737024) |
| Single-run creator UX and real Basilisk finite-run regression | PASS | [43156737048](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737048) |
| Continuous satellite native Basilisk tests | PASS | [43156737018](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43156737018) |

## 3. 关键测试证据

- **真实 Chromium 工作台回归：** 37 passed，2 warnings。覆盖对话式入口、图形建模助手、模块拖拽、节点移动、恢复推荐流程、自动布线、后端正式校验、撤销/重做、工程导入导出、任务中心操作及四个专业工作区返回。
- **Agent 输入与运行一致性：** 456 passed + 19 passed，验证自然语言请求、配置投影、pending generation、Run 入口所有权和手工修改保留。
- **有限时长 Basilisk：** 单次与分段输出回归通过；finite-workbench-browser 与 finite-basilisk-physics 均 PASS。
- **原生 Basilisk 持续回归：** native whole-spacecraft 37/37 步骤通过；Battery C++ contract 9/9；Windows Job Object lifecycle 8/8。
- **长时证据：** 8+ 秒墙钟观测、30+ 秒真实墙钟耐久、180 simulated seconds soak、3600 simulated seconds TC/TM soak 均 PASS。
- **依赖来源：** 官方 Basilisk wheel / clean bootstrap 的 Linux、Windows 验证在前置 PR #113 中通过。

## 4. 基线依赖与最终合并

- PR #113 已于 2026-11-12 合并 main，merge commit：`b2fa3ba6ca132aad19f9b85d472dfa68cffd3cc2`。
- PR #114 已从原基线分支改为以 `main` 为目标分支。
- 本记录提交会触发一次基于最新 main 合并基线的完整 CI 复验；该次复验作为 PR #114 最终合并门槛。此前 `ee6663e` 的 7/7 通过结果保留为功能基线证据。

## 5. 阶段边界与后续工作

P6 关闭范围为 P6-A～P6-D 第一批，聚焦入口、引导、上下文保留和原生专业功能衔接。以下明确属于后续增强，不记作本阶段已完成：
- 任务运行历史结果对比、状态异常解释和批量管理。
- 自然语言候选 TaskSpec / 图形结构差异预览与确认应用。
- 专业工具间共享任务/Run 上下文摘要与跨工具结果引用。

以上三项转入 P7。P6 的界面和回归通过不构成全部物理模型、所有故障组合或飞行资格认证。
