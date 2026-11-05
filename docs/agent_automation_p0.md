# Agent 自动化 P0：输入与运行一致性

日期：2026-11-05。开发基线：`35af5943f917a4aca473d4af0f9335a5f007cc6e`。

这是自动化升级方案的第一批交付，处理用户指令、已接受配置和实际运行输入之间的一致性。现有 Canonical TaskSpec、参数守卫、任务中心、执行规划和 Run Bundle 继续作为主链路。

## 用户可见的行为

- 在 Agent 页已有 60 秒配置时，输入“改为 120 秒”再点击运行，会先生成、校验并应用当前输入，成功后提交 120 秒配置。
- 生成失败、表单投影失败、回答过期或输入在校验期间改变时，本次运行请求停止。失败候选不会替换上一次已经接受的配置，待处理输入仍保留。
- 输入为空时可以重跑已接受配置。Agent 按钮与运行按钮继续共用同一个生成所有权，重复点击不会产生第二次生成。
- 表单被手工修改后，增量生成先校验当前表单，并以它作为修改基线。
- 增量修改可以加入基线中原先省略的可选参数。例如明确要求 `solar_efficiency=0.3` 时，只接纳模型候选中实际提供的、能力注册表声明的对应值；无关参数仍保留基线。缺失或错误的候选值继续交给现有显式参数守卫拒绝。已有基线参数保持原有增量修改语义；注册表限制针对新引入的键。

## 生成来源与运行绑定

`POST /tasks/parse` 已返回 `request_id`，并持久化生成报告。`POST /runs` 新增可选字段 `source_request_id: string | null`：

1. 有来源 ID 时，在保存任务和创建运行包前核对生成报告。报告必须记录成功，TaskSpec 必须符合严格规范，声明的 SHA-256 必须与报告中的任务及提交任务一致。
2. 无来源 ID 的既有手工/API 调用保持原有路径。
3. 前端只在成功且仍有效的回答应用后记录来源，并将其绑定到服务器提供的规范化 JSON 文本。手工或 JSON 修改使配置不再匹配时，不沿用旧来源；新的成功表单校验可以产生新的来源。
4. 已验证的 ID 写入运行包现有 `input/provenance.json` 的 `source_request_id`。同文件已有 `task_spec_sha256`，并处于现有 manifest/seal 完整性范围内。

来源绑定说明某次运行提交的配置对应哪次成功解析。它依赖受信任服务器保存的报告；不是对任意本地文件系统篡改的真实性证明。服务器原有规划与执行校验仍继续执行。

### 失败响应

来源校验失败返回 HTTP 409，响应 `detail` 包含具体 `reason_code` 和说明。失败发生在任务和运行包写入之前。

| reason_code | 含义 |
|---|---|
| `TASK_SOURCE_INVALID_ID` | ID 格式不正确或来源路径越出任务目录 |
| `TASK_SOURCE_MISSING` | 来源生成报告不存在 |
| `TASK_SOURCE_INVALID_REPORT` | 报告不可读、JSON/TaskSpec 结构错误或摘要格式错误 |
| `TASK_SOURCE_NOT_SUCCESSFUL` | 来源没有记录成功解析 |
| `TASK_SOURCE_HASH_MISMATCH` | 来源报告的摘要与其规范化任务不一致 |
| `TASK_SOURCE_TASK_MISMATCH` | 提交的运行配置与成功生成的配置不一致 |

ID 是服务器返回的 32 位小写十六进制字符串。显式传入空字符串会被拒绝；省略或 null 表示既有无来源提交。

数值与文本处理复用现有规范化传输，保留整数/浮点数和权威 JSON 文本的精度关系。读取来源时使用紧凑 UTF-8 JSON，避免中文转义放大文档；既有 1 MiB 单份规范化文本上限继续生效。

## 验证记录

本地环境为 Python 3.12。组合回归在三项生产改动合并后执行；后续文档整理不改变生产代码。

| 检查 | 结果 | 范围 |
|---|---|---|
| 合并后的契约与控制流程回归 | **393 passed**，2 条已有依赖弃用警告 | Agent 实际函数、来源接口、规范化精度、任务中心、封存、参数/事件守卫；含 CI 诊断后补充的 3 项实际函数回归 |
| 三项独立任务审查与整分支审查 | 全部通过，无遗留问题 | 中文文本膨胀问题已修复并复审；合并状态仍需看当前提交的 CI |
| JavaScript 语法与 Git 空白检查 | 通过 | 应用脚本与 Node 行为测试脚本 |
| 脚本与依赖治理 | 0 errors / 0 warnings | 97 个脚本；工作流 YAML 可解析，18 个引用测试文件均存在 |
| 本地真实 Chromium | 启动阶段被环境阻断 | `socket() failed: Operation not permitted`；未进入应用断言 |
| 全项目 pytest | 收集阶段停止，1 error | `tests/test_native_thermal_environment.py` 导入失败：`No module named 'Basilisk'` |

两条依赖警告来自既有 Starlette/httpx 与 anyio BlockingPortal 弃用提示。没有通过修改依赖锁定来消除它们。

组合回归命令：

```sh
python -m pytest -q \
  tests/test_agent_pending_input_vm.py \
  tests/test_task_source_binding.py \
  tests/test_explicit_optional_parameter_refinement.py \
  tests/test_canonical_transport.py tests/test_canonical_transport_api.py \
  tests/test_task_center.py tests/test_run_bundle_seal_binding.py \
  tests/test_natural_language_refinement.py tests/test_refinement_transaction.py \
  tests/test_explicit_user_parameter_contract.py tests/test_explicit_parameter_output_guards.py \
  tests/test_refinement_units_and_intent.py tests/test_refinement_negated_clauses_and_dimensions.py \
  tests/test_refinement_event_preservation.py tests/test_explicit_event_request_guards.py \
  tests/test_explicit_no_event_constraints.py tests/test_workbench_agent_integration.py
```

全量尝试命令为 `python -m pytest -q --maxfail=1`，其上述收集错误不能作为全项目通过的证明。

专用工作流 `.github/workflows/agent-automation-p0.yml` 分别执行契约/Node 测试与真实 Chromium 回归，上传 JUnit 证据。它显式设置 Playwright 浏览器路径，避免因漏配路径而跳过浏览器用例。远端验收状态以 PR 当前提交的检查结果为准。

测试范围严格区分：Node VM 执行实际应用函数，但 DOM/网络边界为夹具；API 用例使用真实解析与仅准备运行；封存测试验证完整性结构。模型生成协议夹具和 EPS 参数夹具均不能作为真实模型质量或原生物理仿真验收证据。

## CI 环境与验收准备

PR #89 首轮远端检查发现：`ubuntu-latest` 已使用 Ubuntu 26.04，现有 `playwright==1.57.0` 在安装 Chromium 时不支持该平台；连续单元/原生任务缺少项目已声明的 `psutil`，有限原生任务缺少已声明的 `numba`。本次将 Agent P0 与单次运行创建器的浏览器任务固定到 Ubuntu 24.04，并在受影响的依赖切片补齐现有 `psutil==7.2.2`、`numba==0.63.1`。依赖版本、测试选择、真实 Basilisk 门禁与物理阈值均保持原值；修复后的远端验收仍待当前提交的 CI 运行确认。

后续真实原生检查在 `spice_wmm` 初始化处失败。已核实两个原生工作流没有准备该模式严格要求的 SPICE、WMM 和 J2 文件；运行时只读取本地资源，不会回退到已安装 Basilisk 的资源目录，也不会下载。两个原生任务现于测试前调用仓库已有 `scripts/fetch_environment_assets.py`，沿用既有来源、格式及摘要校验，然后构造真实环境 loader 检查解析路径。资源预检与后面的原生仿真测试分别执行，预检成功不等于物理仿真验收成功。

来源绑定以**最终提交的规范化配置**为准。例如把 JSON 中的 `60.0` 写成 `60`，真实校验接口返回的规范化文本仍与原配置一致时，可以保留原来源；改为 `90` 秒等真正改变配置的操作不能沿用旧来源。浏览器回归同时覆盖这两个方向，异步请求断言等待实际捕获完成，并保留未应用输入的状态提示检查。

各轮运行结果和当前提交的验收链接在 [PR #89](https://github.com/clonetimer/SatAgentSimulation/pull/89) 中更新。

## 后续阶段

本批完成 P0 的输入执行一致性基础。P1 的实验工具、调用预算、幂等与运行协调，以及后续 GoalRun 编排、知识检索和模型路由，需要分别实现和验收。
