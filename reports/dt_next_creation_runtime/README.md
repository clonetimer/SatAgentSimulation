# DT 五入口、三模式原生验收

代码候选为 `8757abc39a69fe2b2f97b6f0313abc769620fb42`，起点为 `main@1a743b6`。检查对象为注册能力 `whole_spacecraft.unified_native.v1`。本机使用 uv 环境、Chrome 和 Basilisk，版本、两类实时服务的部署配置及模型指纹见 [environment.json](environment.json)。

## 验收结果

15 个承诺支持的整星组合均通过。每格链接包含该次 Run 或 Session 的标识、归档验证、三轴首帧及遥测 SHA-256。离线均从历史运行恢复场景，限时实时均自动结束，持续实时均从同一会话重连并主动停止。

| 创建方式 | 离线仿真 | 限时实时仿真 | 持续实时仿真 |
| --- | --- | --- | --- |
| JSON | [通过](json_offline.json) | [通过](json_bounded.json) | [通过](json_continuous.json) |
| 表单 | [通过](form_offline.json) | [通过](form_bounded.json) | [通过](form_continuous.json) |
| 图形拖拽 | [通过](graph_offline.json) | [通过](graph_bounded.json) | [通过](graph_continuous.json) |
| 数字场景 | [通过](scene_offline.json) | [通过](scene_bounded.json) | [通过](scene_continuous.json) |
| 自然语言 | [通过](natural_language_offline.json) | [通过](natural_language_bounded.json) | [通过](natural_language_continuous.json) |

三次自然语言请求使用本机微调模型，同一条指令每种模式各调用一次，首答均通过，无重试、本地修复或回退。每次服务端返回 9,911 个输入 token 与 422 个输出 token，三个派生总量合计 30,999。离线、限时实时、持续实时的模型调用耗时分别为 74.7、40.2、54.6 秒。这个样本验证该任务的创建链路，模型收益与泛化仍需独立评测。

整套验收与相关回归 95 项通过，单独的向导回归 13 项通过，零跳过。负向用例验证原生反作用轮单机工作流仅提供离线配置，两种未支持的实时请求均被服务端拒绝且没有创建会话。异常试验终止真实的被监督 Basilisk 子进程，验证旧会话进入失败终态并保留命令证据，随后以新会话重建并成功完成。[测试计数与 JUnit 指纹](checks.json)

本期修复了参数面板刷新时意外折叠、自然语言“记录间隔”未绑定采样时间，以及省略显式历元时历史轨迹无法回放三个问题。最后一项改用该次封存运行的原生历元恢复轨迹。[原始复现摘要](regressions.json)

## 工况与判据

整星初始机体系角速度为 `[-0.003, 0, 0.006] rad/s`，首帧必须出现在仿真零秒，逐轴绝对误差上限为 `1e-14 rad/s`。三种模式使用 UTC 历元 2026-01-01 00:00:00、零种子配置、0.1 秒积分步长和 0.5 秒遥测间隔。有限工况为 20 秒，包含首末帧共 41 帧。持续模式创建时的 120 秒是初始化时域，由用户停止决定实际末帧。角速度取刚体真值通道；陀螺仪的模型初始化样本另有有效性语义。

| 入口 | 实际操作链 |
| --- | --- |
| 表单 | 向导选择整星，展开模型参数，逐轴输入。实时模式从专业建模的模式按钮审核导入当前配置，确认后创建。 |
| JSON | 粘贴完整 TaskSpec，校验并应用。离线核对提交与原始规格的仿真、模型、参数、事件和输出字段；实时走审核导入。 |
| 图形拖拽 | 拖动配置节点，修改节点检查器中的时间参数，从“全部参数”编辑三轴初值，返回画布校验。离线由画布直接提交；实时使用工作台的模式选择器。 |
| 数字场景 | 离线由场景创建菜单进入向导，重新选择整星并设置参数。实时在场景对象树选择整星并修改其参数，从场景菜单进入实时页，显式导入已编辑的整星配置。 |
| 自然语言 | 工作台向本机真实微调模型提交同一请求，检查生成候选并应用。离线核对封存和历史回放；实时走相同的审核导入，分别新建会话。 |

每个限时实时用例检查暂停时仿真时间不变、单步前进一个量子、继续、遥控进入安全模式的最终回执及载荷使能遥测、20 秒自动结束。持续实时用例重新加载工作台，连接原会话后主动停止；两个模式均要求服务端明确返回 `COMPLETED` 和 `SEALED`，并核对遥测清单的 SHA-256。

## 复验方法

在候选提交的工作区准备 [本地场景资产生成脚本](../../scripts/vendor_scene_assets.py)指定的静态资产和 [模型服务](../../scripts/serve_modelscope_model.py)所需的本地基座及适配器。模型服务应全程监听独立回环端口。将下列环境变量绑定到本机实际路径，模型用例串行执行。

| 环境变量 | 用途 |
| --- | --- |
| `PYTHONPATH` | 当前候选的 `src` 绝对路径 |
| `SAT_SIM_BROWSER_EXECUTABLE` | Chrome 可执行文件 |
| `SAT_SIM_P9L4_NATIVE_UI_TEST`、`SAT_SIM_P9F_NATIVE_BROWSER_TEST` | 均为 `1`，启用实际浏览器和原生后端 |
| `SAT_SIM_DT_MODEL_NAME`、`SAT_SIM_DT_MODEL_URL` | 本次为 `dt-qwen25-coder15-v5`、`http://127.0.0.1:18031/v1` |
| `SAT_SIM_P9L4_EVIDENCE`、`SAT_SIM_P9F_EVIDENCE` | 分别指向新建证据根目录下的 `offline` 和 `live` |

```powershell
uv run --active --no-sync python -m pytest -v -p no:cacheprovider --basetemp .tmp/dt-repeat-work --junitxml=.tmp/dt-repeat/results.xml tests/test_p9l4_offline_native_ui_browser.py::test_real_whole_spacecraft_offline_run_created_in_ui_and_restored_in_scene tests/test_p9f_native_browser.py::test_native_user_created_session_drives_actual_globe_renderer tests/test_explicit_parameter_output_guards.py tests/test_refinement_units_and_intent.py tests/test_native_initial_body_rate.py tests/interactive/test_supervised_continuous_native_runtime.py::test_live_session_child_crash_preserves_active_sqlite_owner_fence
python reports/dt_next_creation_runtime/collect.py .tmp/dt-repeat .tmp/dt-repeat-work 8757abc39a69fe2b2f97b6f0313abc769620fb42
```

每次运行使用新的证据目录与 pytest 临时目录。本次对应 `.tmp/dt-matrix-8757abc` 与 `.tmp/dt-matrix-8757abc-work`。浏览器报告、截图、模型调用和原生制品保存在这两个目录。汇总器读取实际遥测逐帧核对 0.5 秒网格，允许时间戳从整数纳秒转换为浮点秒时产生一个浮点表示单位的差异，本次最大偏差为 1.78 × 10⁻¹⁵ 秒。复验前应确认检出提交与传给汇总器的提交号一致。

## 上期移交

电池五测点和安装热边界、任务级外部精度、目标星实装映射和指定部署环境性能继续按[原计划](../../docs/engineering/dt-next-creation-runtime.md)的输入条件跟踪。此处的结果适用于本机所列整星工况。
