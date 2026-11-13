# P9-A 对象、参量与本地实时入口实施计划

> 执行方式：本会话逐项实现；每项先观察失败测试，再实现并复验。

**目标：** 落实已批准的 C 方案对象定义，建立可验证的参量字典，修复本地启动未启用实时服务的问题。P9-B 三维场景不在本批范围。
**架构：** 在现有目录与 Capability Registry 之上增加纯展示契约，不复制物理模型。统一的本地启动模块仅为回环地址提供实时默认配置，保留显式禁用和监督策略。
**技术栈：** Python、现有 Schema/Registry、pytest、原有 FastAPI 与 Windows/Unix 启动脚本。
**设计依据：** PR #121 / 9408649，`2026-11-13-p9-globe-centered-workbench-design.md`，以及用户已确认的对象定义 C。

## 全局约束

- 保留 V15 专业入口、全部现有 capability ID、原始字段和单位；不修改动力学或会话批准策略。
- 姿态控制、轨道与空间环境、轨道控制分别建模为用户对象；轨道控制不是推力器，不计入物理分系统数量。
- 不制造坐标、GLB 部件位置或独立轨道控制能力；没有来源的参数不进入已审定字典。
- 开发术语只出现在文档/诊断中；面向用户的名称使用“单机/对象 + 参量”。
- 默认服务接口仍关闭实时功能；仅本地启动器在回环地址且用户未显式设置时启用。

## Task 1：P9 对象契约

文件：新增 `src/sat_sim/presentation_objects.py`；修改 `workbench_catalog.py` 的 ADCS 显示名称；新增 `tests/test_p9a_objects.py`。
接口：`build_scene_object_catalog(presentation, policy, *, service_enabled)` 返回 P9 对象与分类型计数；只消费已注册目录与服务端策略。
- [x] RED：断言姿态控制中文名、独立轨道基础对象和轨道控制功能对象、父子完整性、计数与不可执行轨道控制；断言部署关闭、监督式限制、未知策略均不能冒充可用。
- [x] GREEN：实现显式父级、空间选择语义和模式状态；不自动把单机切成整星。
- [ ] VERIFY：`python -m pytest -q tests/test_p9a_objects.py tests/test_component_primary_capabilities.py::test_all_24_components_have_active_direct_primary_capabilities`。

## Task 2：单机—参量字典与覆盖审计

文件：新增 `src/sat_sim/quantity_dictionary.py`、`tests/test_p9a_quantities.py`、`scripts/audit_p9a_catalog.py`。
接口：`quantity_catalog(capability_id)` 返回已审定条目与未审定覆盖清单；`validate_dictionary()` 校验源字段、单位与重复；归属由显式条目及行为测试审定。
- [x] RED：反作用轮 13 项结果指标与 13 项数值曲线均有明确命名；索引 0 是 1 号轮而非 X 轴；群组量不能归给单个轮；未知字段保留未审定状态。
- [x] GREEN：实现显式映射及源契约校验，首批完整覆盖反作用轮；导出全部对象和字段的审核清单，未审定项不伪称已完成。
- [ ] VERIFY：字典测试、现有参数展示测试、运行审计脚本并检查 JSON/CSV 来源与数量。

## Task 3：实时入口根因回归与本地启动修复

文件：新增 `src/sat_sim/local_workbench.py`、`tests/test_p9a_local_workbench.py`；修改 `scripts/start_local.sh`、`scripts/start_local_windows.ps1`。
接口：`configure_local_environment(host, environ)`；`main(argv)` 复用原 `agent_cli`，不更改全局 `create_app()` 默认。
- [x] RED：实际启动配置回归 `/health`、`/interactive/capabilities`；验证普通本地启动开启实时目录，显式关闭/非回环地址/监督式配置仍生效。
- [x] GREEN：只修复启动配置，不放宽运行时能力策略。PowerShell 返回真实进程退出码。
- [ ] VERIFY：上述测试；既有交互 API 与会话测试；Windows CI 测试；原生物理回归由现有 CI 独立验证。

## 交叉接口与复核重点

对象契约消费服务端策略；字典消费真实 registry；启动修复只改变本地缺省值。三者均不拥有第二套执行器。
复核：单机多实例索引、群组/标量归属、监督式限制、显式 false/空值、非回环地址不可自动启用、缺失能力不自动替代、旧 V15 目录计数兼容。
本地全量 pytest 至少尝试一次；依赖缺失、跳过、环境不一致逐项记录，不能用专项通过代替全量通过。

## 执行记录

- 基线 main：7dffea0；设计确认：9408649。
- Git 直连不可用，使用当前独立分支 CI 的 `git archive` 取得只含跟踪文件的源码快照；核验提交 ae575f45 与 SHA-256 后建立隔离工作树。未触碰用户本地目录。
- 当前本地环境不含 Basilisk、sgp4；原生执行的判定以真实 CI 结果为准。

- 命名覆盖：43 个当前可见能力合同，已审定 35 项（反作用轮），另有 1507 项已列入来源清单但未审定；P9-A 尚未整体收尾。
- 真实原生首帧新增独立 CI 门禁（姿态限时、整星限时、整星持续）；契约 CI 不运行部件物理模板套件，以免将非原生参考模型成功当成本轮原生证明。
