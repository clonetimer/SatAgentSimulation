# P9-A：独立效能评估边界与对象树第二版

## 本轮基线

PR #124，开始核查时 head 为 `54319be218cb5d8bbd359865e29d898c891fd636`。
该提交 8 组实际执行工作流成功，主干集成候选工作流跳过。这里的绿色不等于 P9 全部新增测试已执行。
新增 `P9 ownership and independent evaluation contracts`，把 P9 专项及后处理测试显式纳入 Ubuntu/Windows 并行任务。

## 实际发现与修复

1. 补跑旧 P9 专项发现 `QUANTITY_SOURCE_FIELD_MISSING:summary:adcs.mtb.max_torque_norm_nm`：修正字典三个摘要字段为合同中的 `qoi.adcs.mtb.*`，不改中文名、单位、数据或物理模型。
2. 旧对象树测试用 `row["object_id"]` 遍历参量分组而触发 KeyError；改为区分对象节点与分组节点，并断言显式 `owner_object_id`。
3. 对象树 `workbench-object-tree.v2` 不再读取/展示 summary_options，历史 summary/QoI 仍保存在原合同和运行制品中。摘要变化或摘要解析失败不会改变该树。
4. 增加父子环、孤立链路描述校验，防止关联对象在分组时悄然丢失。
5. pyproject 缺少三个命名字典 JSON 的 package-data：补齐并实际构建、独立安装 wheel，确认安装目录可加载 24 类组件字典。

## A：后处理契约

`performance_evaluation.evaluate_snapshot(snapshot, plan)` 是纯只读计算函数，不导入运行器、会话或队列，不写 TaskSpec 或运行状态。
输入包括 run_id、snapshot_id、封存标记、原始采样序列和精确来源绑定；调用方负责从真实运行记录构造快照，本函数本身不是数据真实性认证。

首批仅支持显式版本化公式：采样算术均值、采样均方根、最大绝对值、末值。它们是后处理统计，不自动等同于效能结论。
方案指定时间窗、来源对象、能力、字段、单位和最小采样数；阈值由用户/开发者显式给出。没有判据时只给数值，不判合格。
缺数据、来源/单位冲突、时段不全、采样间隔不合要求返回未评估；NaN、非法方案或未封存快照拒绝计算。不同阈值可重评同一快照，不触发仿真。
报告带 source_digest、plan_digest、方案版本、公式 ID、实际样本数量及数据窗口。

## B：对象树契约

整星、分系统、单机、环境、设施、链路仍可各自拥有物理参数和过程输出。
仅移出“结果指标”/summary_group，不移除运行安全检查或过程中的控制误差、估计量。
旧 summary 计数暂保留为固定 0 的兼容字段，并显式说明其语义。包含关系与跨对象关系分开校验。

## C：界面消费契约

新增 `web/object-workspace.js` 只读视图适配器；仿真面板消费树 v2，评估面板单独消费报告 v1，并校验报告所属运行。
适配器只产出展示数据，不提交运行、不自动修改参数。使用真实 Python 对象树和评估报告驱动 Node 契约测试，而不是各自手写两套假接口。
**它尚未挂载到现有默认工作台；本批不是新版界面或三维场景验收。**

## 验证记录

- 对象树新增测试先失败：结果指标仍在树中、摘要变化会改树、缺失 Owner、安装包未声明字典。
- 后处理 27 项测试先失败（模块缺失）后通过；补充非法公式类型回归后为 28 项。
- 前端适配器测试先失败（模块缺失）后通过。
- 相关合并回归：**63 passed**。命令：
  `python -m pytest -q tests/test_p9a_evaluation_boundary.py tests/test_p9a_object_tree_quantities.py tests/test_p9a_performance_evaluation.py tests/test_p9a_workspace_adapter.py tests/test_conversational_workbench_shell.py tests/test_parameter_presentation.py tests/test_parameter_surface_audit.py tests/test_cross_level_owner_audit.py tests/test_rw_cross_level_semantics.py`
- `node --check src/sat_sim/web/object-workspace.js` 通过。
- wheel 实际构建、独立目录安装及字典导入通过。未安装 Basilisk，不以此声称原生仿真通过。
- 尝试全量 pytest，收集阶段因缺少 Basilisk 中断，共 6 个文件：
  `test_native_thermal_environment.py`、`test_p0_trustworthiness_fixes.py`、`test_resource_closure_agent_trust.py`、`test_thermal_radiator_heater_ab.py`、`test_whole_spacecraft_adcs_eps_dataset_generator.py`、`test_whole_subsystem_owned_feasibility.py`。
- 本次为作者自查；没有独立审查代理。远程结果以最终提交的新 CI 为准。

## 尚未完成的迁移

旧向导的 QoI 选择、独立评估入口、报告持久化/API 及用户确认后的配置复制流程还需联调；不能仅靠从对象树删掉一个分组就宣称单次仿真全链路已完全解耦。
已有运行安全检查、任务验证和历史制品不删除。PR #122 的本地实时入口修改尚未与本分支统一验收。
多实例实际配置回写、链路端点实绑定、整星/分系统字段逐项审定仍是原 P9-A 后续工作，不作为本次完成项。
main 保持不变，不合并、不改仿真物理方程。
