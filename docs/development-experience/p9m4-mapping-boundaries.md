# P9-M4 映射边界与真实证据接入经验

日期：2026-11-17。基线：PR #158 的 11 项契约测试通过，不等于所有异常输入已经覆盖。

## 已复现的缺口

旧验证器只检查目标视图是否包含于 views：参考 CAD 同时声明 reference_cad/as_built 仍获 valid。views 为字符串时会被拆成字符列表；哈希字段只检查非空，`x` 也被接受。仿真遥测声明没有与 Run/TaskSpec 身份做一致性检查。

处理：视图必须为去重字符串列表并与映射类型精确一致；SHA-256 必须是 64 位十六进制；参考 CAD 禁止夹带仿真绑定或批准安装 frame；遥测必须匹配仿真绑定的 Run ID 和 TaskSpec 哈希。非法对象类型返回明确 ValueError，而不是 AttributeError/TypeError。

## 不把自声明当作审批认证

五类 evidence 的 approved 字符串只是待验证声明。返回值明确标记 `validation_scope=declaration_consistency_only`、`approval_authenticity_verified=false`。实际批准来源/审查人认证仍须独立信任域；本轮不建立自动审批功能。

## M3 → M4 的真实输入边界

输入包括固定 CI 产物中的审查包、workbench-object-tree.v2、STEP 装配树、转换报告和实际 GLB 字节。CI 在消费前用独立清单检查五个文件的 SHA-256；注册器再核对源 commit、模型路径、节点数、父路径、candidate/node 摘要与 GLB 文件哈希。

必须区分 GLB 哈希、STEP 源哈希、节点证据摘要、审查包 JSON 摘要；不能把任意 64 位摘要当作几何资产身份。当前输出覆盖 19 类物理设备：3 个 reference_cad 候选、16 个 unlocated；外部设施/关系对象不计入设备实例。

## STEP 路径不是 GLB 节点绑定

源装配树和 GLB 层级不完全相同。保留 `node_path_space=step_assembly_occurrence`，`glb_node_binding=null`，逐节点 `renderable=false`。既有完整参考 CAD 查看器可以独立浏览资产，但本注册表不声称已经完成候选节点高亮或遥测联动。

## 验证记录

先复现 26 个策略失败，再修改生产代码；注册器的 16 个用例（含真实包接入）在未实现时失败，再实现。保留原 11 项行为测试。源代码/测试本地快照的 Git blob SHA-1 与连接器读取值一致；本地为相关模块验证，完整包导入和跨平台结果以新 CI 为准。

后续独立任务：GLB occurrence 显式绑定、API/检查器只读展示、浏览器验收、真实三模式长序列；不得将本轮 JSON 注册验收表述为整个平台完成。


## Stable gate maintenance: dependency pin must follow the project declaration

P9-M4 triggered the Stable source-wheel gate and exposed a stale assertion hard-coded to \`bsk==2.11.0+satfix1\`, while the current \`pyproject.toml\` declares \`bsk==2.11.0+satfix2\`. The first attempted repair then read \`pyproject.toml\` relative to \`/tmp\`, because the installed-wheel verification intentionally runs outside the checkout.

Correct pattern:
- Run installed-package checks from an isolated working directory to prove the wheel is importable independently of the checkout.
- Read source metadata through \`GITHUB_WORKSPACE/pyproject.toml\`, not the current working directory.
- Compare the single declared \`bsk==...\` requirement with the installed wheel's \`Requires-Dist\` metadata after whitespace normalization.
- Do not hard-code an obsolete version and do not remove the pin check.

## Scene integration: display evidence class, never fabricate a model

The new read-only \`/workbench/engineering-mapping\` endpoint accepts only a deployment-configured mapping bundle path (\`SAT_SIM_ENGINEERING_MAPPING_BUNDLE\`). It validates the complete bundle before returning a minimal public projection. Missing configuration is reported as \`not_configured\`; malformed bundles are rejected as \`invalid\`; no path or raw evidence content is returned.

The satellite scene inspector displays only mapping counts and object-to-mapping-kind status. It does not load a GLB, infer STEP-to-GLB node selectors, create telemetry bindings, or draw unlocated hardware. This keeps the UI useful while preserving the distinction between reference CAD, simulation configuration and approved as-built geometry.
