# P9-N1 单机工作台开发经验

日期：2026-11-17

## 1. 不要为单机独立运行新建第二套仿真系统

已有快速仿真向导、Capability Form Schema、Scenario Template、TaskSpec、Run Bundle 和注册适配器。正确做法是增加面向用户的单机入口，并复用现有模板 API 与无损表单投影，而不是另写单机表单或执行接口。

## 2. 将「已验证原生单机」与「全部可配置部件」分开

Capability Registry 中有很多可执行 component capability，但它们的来源可能是项目 source-native、工程参考模型或官方 Basilisk。不能把「可独立执行」等同于「官方 Basilisk 原生」。P9-N1 首批单机入口只列入已经通过官方 Basilisk 独立物理与 Run Bundle 验收的反作用轮。

## 3. 单机运行模式要符合实际 runtime lifecycle

单机 component TaskSpec 支持有限时长运行；当前 interactive/continuous runtime 仍是不同的整星运行图。专用单机向导只提供离线有限时长模式，避免向用户展示点击后无法执行的实时模式。

## 4. 后端 provenance 必须在用户入口保持一致

表单默认值、运行摘要、adapter evidence、trace 和 Run Bundle 必须一致声明 `basilisk_native`。项目参考模型仍可用于普通组件实验，但不得静默回退，也不得把默认 project_reference 结果称为 Basilisk 原生。

## 5. 模板是配置起点，不是运行批准

选择模板只生成候选 TaskSpec 并投影到可编辑表单。用户仍须检查时长、采样、参数、输出并通过正式验证；模板应用本身不自动启动仿真。


## 6. UI-originated Run acceptance must verify the actual plotted quantities

The guided Run acceptance now checks more than a successful Run status. The native reaction-wheel template explicitly selects the two physical response curves (wheel-1 angular speed and actual motor torque) and the initial/final/max speed summary indicators. Chromium verifies both chart legend labels after the UI submits a real asynchronous Basilisk Run through the embedded worker. This prevents a false-positive where a Run succeeds but the user is shown an empty or irrelevant chart.

## 7. Keep browser test names unique

The P9-N1 browser suite briefly contained two Python test functions with the same name. Python silently keeps only the latter definition, so the full UI-originated Run journey was not collected. Use unique test names and make the workflow selector include each intended journey explicitly.


## 8. Test nested event controls without bypassing their parent disclosure

The compact event editor is nested inside the guided event panel. A Playwright test that clicks the nested #compactEvents > summary while its ancestor panel is closed will time out because the nested summary is not visible. For a backend policy test, inspect the underlying disabled state and the note's own hidden class; test the full disclosure interaction separately only when the user-facing visibility of that nested panel is the subject.

## 9. Browser smoke should distinguish critical asset/API failures from optional browser resources

Some browser consoles report 404s for optional resources (for example favicon or ancillary resources) that are unrelated to the workbench journey. For this component workflow, collect uncaught page errors and fail on 4xx/5xx responses for required /assets/, form, scenario-template and workbench API paths. Do not hide missing application assets, but do not treat unrelated browser metadata requests as application failures.


## 10. Provenance fields are intentionally collapsed in compact result mode

The compact workbench moves secondary Run details into a disclosure labelled "运行信息". A browser test must open that disclosure before asserting backend/provenance values; the fields are present in the result DOM but intentionally not shown in the compact card by default. This keeps the initial result summary concise while retaining auditable detail on demand.


## 11. Preserve the UI execution lifecycle in browser acceptance

The guided Run button submits `execute: true` with `execution_mode: async`; the frontend expects an `execution_state` and polls the execution endpoint until terminal state. A test that rewrites only the request mode to `sync` while keeping the asynchronous UI handler can create a real completed Bundle but leave the browser polling a queue state that was never created. For a real browser journey, keep the UI request asynchronous and enable the embedded worker; assert the terminal execution state and sealed Run rather than mutating the request into a different API contract.

## 12. History navigation must use a visible product path

A guided wizard owns the primary surface. Its “其他专业工具” disclosure is hidden outside the welcome step, including on the completed-results step. The results surface therefore exposes a dedicated visible “打开运行中心” action. Browser acceptance follows that action; do not click hidden sidebar nodes, force DOM state, or try to reopen a disclosure that is intentionally absent. Test the same Run ID, provenance, chart data and seal after reopening history.


## 13. Preserve the native-only execution boundary after preset application

Applying a Basilisk preset from the general normal-run path must also change the wizard intent to the verified native single-component goal. Otherwise the form can contain a native-only TaskSpec while the wizard still offers realtime modes from the general workflow. The preset handler now sets the component scope and offline mode explicitly; browser acceptance returns to the mode step and asserts that offline is the only available choice.

## 14. Use the current Schema form projection selectors in browser tests

The current form wrapper publishes `data-field-path` and is mounted under `#wizardParameterHost #dynamicForm`. Tests copied from older UI revisions that query `data-path` or assume the form is directly under the page root silently target a stale DOM contract. Browser tests should use the public current wrapper selector and assert the actual nested control (for example the backend `select`), not the wrapper label.


## 15. Separate generic workbench browser dependencies from native-runtime acceptance

The shared single-run browser workflow intentionally installs only the control-plane/browser dependency slice and does not install Basilisk. A browser test that executes a native reaction-wheel Run must explicitly skip when `Basilisk` is absent; the dedicated native-RW workflow installs `bsk==2.11.0`, asserts both distribution and module versions, and then runs that same browser journey without skips. This preserves one real UI journey while keeping the general workbench suite lightweight and preventing a missing optional runtime from masquerading as a product defect.


## 16. Keep the historical Run path available from completed results

The guided wizard hides its “其他专业工具” disclosure outside the welcome step. A completed Run is a results step, so attempting to open Run Center through that hidden disclosure is not a valid user path. The results surface now exposes an explicit “打开运行中心” action. Browser acceptance follows this visible control and verifies the same sealed Run, provenance and recorder-backed curves after reopening.

## 17. Separate UI lifecycle evidence from native physics evidence

The product browser test must exercise the real `execution_mode=async` path and embedded worker, then wait for the Run to reach a terminal state. The native physics integration test separately verifies the Basilisk adapter and sealed Run Bundle. Do not change only the browser request to synchronous mode while leaving the frontend polling contract unchanged. If the UI does not reach a terminal state, inspect the execution endpoint/state and persisted Run evidence before changing timeout values; a longer wait does not repair a missing worker or broken state transition.
