# P9-A 第一批实施与验证记录

## 范围与状态

设计依据：PR #121 / `9408649`，用户确认轨道方案 C。代码基线：P8 `7dffea0`。
本批是对象契约、反作用轮字典和本地启动修复；不是三维工作台交付，不是 P9-A 全量命名完成。

- 新 P9 展示目录：33 个对象，包含 1 整星、6 物理分系统、24 部件、1 轨道/空间环境基础对象、1 轨道控制功能对象。
- 旧 V15 目录的 32 个对象及 capability ID 保留；仅将 ADCS 默认名称纠正为“姿态控制分系统”。
- 轨道控制没有虚构执行合同、参数或部件坐标；与轨道环境、姿态、推进建立显式关系，不重复创建推力器。
- 新字典仅首批审定反作用轮 35 项：13 条曲线、13 项指标、9 项参数。字典尚未接管旧 UI 标签渲染。
- 审计实际读取 43 个能力合同；另有 1507 项源字段列入未审定清单，不自动翻译、不计完成。

## 实时入口根因与修复

原本地 Windows/Unix 启动脚本没有设置 `SAT_SIM_INTERACTIVE_ENABLED`；库默认关闭实时路由。原浏览器测试显式打开该开关，因此无法发现普通启动入口的缺项。

两种启动脚本现在经 `sat_sim.local_workbench` 复用原 CLI：仅在回环地址、且未提供该变量时默认启用实时服务。显式 `0`、`false`、空值和监督式运行策略保持原样；共享/公网监听不自动启用。不改变 API 库默认、不放宽运行时允许范围。

这是按当前源码复现的启动路径问题；没有读取用户机器的实际环境变量，不能排除其本地还存在显式禁用或额外部署限制。

## 本会话证据

| 验证 | 结果 |
|---|---|
| 修改前已有参数展示测试 | 3 passed |
| 新测试首次 RED | 30 failed / 1 passed，缺少契约/字典/本地启动模块及 ADCS 旧名称 |
| 补充 Schema 和服务策略优先级 RED | 3 failed / 14 passed，随后修复 |
| 最终契约与既有相关回归 | 53 passed |
| 原生首帧测试（当前容器） | 3 skipped，未安装 Basilisk |
| 字典来源与单位审计 | 35 绑定全部有效，source_errors=0；未审定 1507 项 |
| 脚本治理 | 103 脚本，0 errors / 0 warnings |
| Python 编译与 bash 语法 | 通过 |

完整 pytest 已尝试，收集阶段因未安装 Basilisk 停止：`tests/test_native_thermal_environment.py`、`tests/test_p0_trustworthiness_fixes.py`、`tests/test_resource_closure_agent_trust.py`。不能宣称全套测试通过。

另发现已有 `tests/test_web_workbench.py::test_workbench_assets_fix_light_input_colors_and_add_task_center` 仍断言已移除的 `data-view="tasks"`。在未修改的基线快照上复现同一失败，非本轮引入；没有删除该断言来制造通过。

## 持续集成门禁

`.github/workflows/p9a-object-contracts.yml` 包含 Ubuntu/Windows 双平台契约测试，以及真正安装官方 Basilisk 的三条首帧测试：姿态控制限时、整星限时、整星持续。每条原生测试必须创建真实会话、启动，并收到仿真时间大于零且数值有限的实际遥测，不能以页面按钮可点击代替。

上述是 CI 设计要求，执行结果以本批提交的工作流和 PR 记录为准。Windows CI 不等于用户 Windows 桌面完整验收。

复验命令：

```bash
PYTHONPATH=src:. python -m pytest -q tests/test_p9a_objects.py tests/test_p9a_quantities.py tests/test_p9a_local_workbench.py
PYTHONPATH=src:. python scripts/audit_p9a_catalog.py --output-dir reports/p9a/catalog
PYTHONPATH=src:. python -m pytest -q tests/test_p9a_native_launch.py
```

## 复核与未完成项

已作独立一轮作者自查（当前没有独立审查代理）。复查源字段/单位、飞轮实例与群组量、显式禁用、非回环监听、监督策略、重复/未知策略条目、原目录兼容及测试与实际启动链的差异。没有把自查称为独立代码审查。

P9-A 后续仍需审定其他对象的 1507 项字段及真实多实例绑定；三维资产、场景 API/UI 接线、右侧对象面板属于后续阶段。本批保留 Draft，不自动合并主干。
