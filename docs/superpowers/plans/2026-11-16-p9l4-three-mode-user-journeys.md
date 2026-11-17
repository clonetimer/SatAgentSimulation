# P9-L4：三种仿真模式完整用户旅程

日期：2026-11-16  
基线：P9-L3 已合入 main（merge commit `4b0e5f6b77e620cdd883c28ddadb752987ca1cc6`）

## 目标

以真实 Chromium、真实 API 和官方 Basilisk 2.11.0 验证三种运行方式在用户工作流中的入口、配置/创建、启动或执行、数字场景关联、状态/结果查看与退出行为。沿用既有 TaskSpec、Run Bundle、实时会话和遥测契约，不增加平行执行状态机。

## 三条旅程

1. **离线单次仿真（finite / sealed Run）**
   - Chromium 通过快速仿真向导选择整星和离线模式，编辑时长、执行正式 TaskSpec 校验并提交真实 `POST /runs`。
   - 由现有嵌入式执行队列调用官方 Basilisk 完成有限 Run，浏览器等待 SUCCEEDED/PASS。
   - 验证 Run Bundle SEALED、完整性校验通过，再从运行结果进入场景。
   - 浏览器从运行历史选择该 Run，进入卫星数字场景。
   - 核验 Run 身份、封存质量、轨道/姿态字段及历史轨迹；不以 fixture 遥测替代物理结果。
2. **限时实时仿真（bounded session）**
   - Chromium 经产品界面选择能力、配置时长/步长并创建会话。
   - 用户启动真实 Basilisk 会话，再进入数字场景。
   - 核验同一 session 的 whole.orbit 与 whole.adcs 流、真实 sequence/time/GOOD 质量、姿态真值和传感器检查器。
   - 结束时显式停止会话并核验资源清理。
3. **持续实时仿真（continuous session）**
   - Chromium 经持续仿真界面创建、启动真实受监管 Basilisk 会话。
   - 进入数字场景并核验来源 ID、lifecycle、轨道/姿态与 ADCS 关联。
   - 显式停止会话；不得将持续模式的停止/中断误报为成功完成。

## 当前复用的权威验收

- `tests/test_p9f_native_browser.py`：真实浏览器创建 bounded/continuous 会话，真实 Basilisk 执行、产品 WebSocket/REST 遥测、Globe.gl 渲染；另有真实有限 Basilisk Run 封存后在浏览器历史场景恢复姿态的桌面/窄屏测试。
- `tests/test_p9l4_offline_native_ui_browser.py`：新增真实 Chromium 向导创建整星离线任务、真实 POST /runs、嵌入式执行队列、官方 Basilisk、封存校验及场景恢复闭环；不使用 API fixture。
- `tests/test_p9c_satellite_scene.py`：离线场景资源、场景导航、任务创建对话框、实时服务不可用反馈、场景/专业建模切换及浏览器错误审计。
- `tests/test_p7d_complete_user_journey.py` 与 `tests/interactive/test_single_run_creator_ux.py`：快速仿真向导的用户交互、配置校验、运行结果 UI、返回配置和旧功能兼容性。
- `tests/interactive/test_unified_segmented_execution.py`：原生有限/分段执行物理回归。

## P9-L4 新增门禁

新增 `.github/workflows/p9l4-three-mode-journeys.yml`，将向导/场景对话框浏览器旅程与真实 Basilisk 三模式场景旅程放入同一候选提交的 CI。原生 Job 必须使用官方 Basilisk 2.11.0、离线环境资源和本地 Globe.gl 资产；同时运行真实 UI 创建的整星有限 Run 和两种真实实时会话，保存浏览器报告及截图。

## 真实性边界

- P7-D 向导测试中的 API 路由 fixture 仅验收前端工作流，不计为物理执行证据。
- P9-L4 的离线有限 Run 另由真实 Chromium 产品向导提交到真实 API 和执行队列，并由官方 Basilisk 执行、封存；不得以 P7-D fixture 替代。
- bounded 与 continuous 的创建、启动和遥测链路由真实 Chromium 产品 UI 驱动。
- 演示遥测、前端注入帧、旧提交结果和被跳过的 Job 均不得计入 P9-L4 PASS。
- 本工作流把既有权威测试组合为同一提交的综合门禁，不声称新增了一套仿真后端。

## 合并标准

- 两个 P9-L4 Job 均 PASS，0 个失败/错误/跳过的必需用例。
- P9-C、P9-D、P9-E、P9-F、P9-G、P9-H、P9-I、静态检查和原生整星长序列在同一 head SHA 上通过。
- 页面错误、控制台错误、HTTP 4xx/5xx 和请求失败均为 0。
- 证据包可追溯到该 head SHA；桌面与窄屏均通过。
