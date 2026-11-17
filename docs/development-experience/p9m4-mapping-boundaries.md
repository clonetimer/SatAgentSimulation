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


The first workspace-path correction still failed because the stable check's Python heredoc did not import \`os\`. The traceback correctly identified \`NameError: os is not defined\`. When moving checks into an isolated \`/tmp\` working directory, explicitly audit the complete imports used by the snippet (including environment access); do not assume a prior module-level import is in scope inside a standalone heredoc.


## Do not trust an arbitrary mapping bundle path at runtime

The first scene endpoint draft validated a JSON bundle's internal schema but did not pin the file bytes or restrict its intake scope. That could allow a hand-authored bundle to claim an \`as_built_verified\` count based only on self-declared evidence metadata.

The endpoint now requires a separately configured SHA-256 for the exact bundle file and accepts only the pinned reference-registration scope. It additionally enforces zero as-built instances, zero telemetry bindings and zero node-renderable candidates. A future approved as-built registry requires a separate trust/approval workflow; it must not be enabled by simply changing a JSON field.


## Cross-platform workflow commands: keep pytest and Node checks in separate YAML block lines

The first M4 API/UI workflow revision placed a second \`node --check\` command under a single-line YAML \`run:\` scalar. On Windows PowerShell, it was appended to the pytest argument list, producing \`pytest: unrecognized arguments: --check\` before tests ran. Use \`run: |\` for multi-command steps and keep each command on its own indented line. Verify the resulting workflow text after editing; a visually intended newline in a string replacement is not proof that YAML contains a block scalar.


## GLB crosswalk candidate audit: parse the whole container and fail closed on incomplete ancestry

The first STEP-occurrence → GLB-node candidate audit extracted the JSON chunk after checking only the GLB header and JSON chunk length. That was sufficient for the happy-path fixture but did not validate the complete chunk envelope. It also treated a missing STEP parent name as if no parent constraint existed, allowing a unique child-name match to become a candidate despite incomplete ancestry evidence.

Correct pattern:
- Walk every GLB chunk from byte 12 to the declared file end; require JSON as chunk 0, bounded/aligned chunk lengths, no duplicate JSON, and BIN only as chunk 1 when present. Ignore unknown extension chunks after the standard leading chunks.
- Validate node mesh references, child indices, multiple-parent conflicts, hierarchy cycles and scene-root references before producing any inventory.
- A candidate requires a non-empty STEP node name and complete parent identity. A STEP root must match a GLB root; a child with missing/unresolved parent name is classified as parent identity incomplete, never a candidate.
- Preserve candidate-only status. Even exact unique node+parent name matching does not prove geometry identity, datum, transform or installed hardware; formal selector stays null and rendering stays disabled until visual review.
- Add negative tests for missing parent name, root-vs-child mismatch, malformed chunk bounds and hierarchy cycles. Hash receipts must be updated in the test when mutating GLB bytes, so the test reaches the structural validator rather than stopping at the earlier digest gate.

The Khronos glTF 2.0 specification requires the JSON chunk first, the BIN chunk second when present, and bounded/aligned chunks; bufferView/accessor offsets and ranges must also be checked by geometry auditors. Use the official specification as the format authority, not assumptions based on one exported GLB sample.


A further crosswalk guard is required: a unique node name and parent match is not a useful geometry candidate if the node is only a group or has no mesh. Require the referenced mesh to exist and contain at least one primitive; classify a unique meshless name as matched_node_without_mesh with no candidate index. This still does not prove the mesh corresponds to the STEP occurrence; it only prevents obvious non-geometry nodes from entering the visual-review queue.


## Real assembly occurrence nodes may be groups, not mesh-bearing leaves

The first real M4 crosswalk report classified the battery, reaction-wheel and payload occurrence names as matched_node_without_mesh. Inspection of the pinned BIRDS-4 full assembly GLB showed these are intentional assembly group nodes: battery node 545 owns 14 mesh-bearing descendants; reaction-wheel node 560 owns 24; the perovskite panel node 759 owns one. Rejecting all meshless nodes therefore discarded the correct assembly-level occurrence identity.

Correct pattern:
- Keep rejecting a meshless leaf with no geometry descendants.
- For a unique exact-name + exact-parent group node, traverse its validated child subtree and collect mesh-bearing descendants.
- If the subtree has geometry, emit a group-level candidate with the group node index plus a complete descendant mesh inventory; do not pretend the group itself has a mesh.
- Keep formal selector null, rendering disabled and visual review required. The candidate establishes a plausible assembly occurrence grouping, not geometry identity or installed-unit approval.
- The real pinned 855-node BIRDS-4 test must assert the three known reference groups resolve to group candidates and enumerate descendant meshes; synthetic fixtures alone cannot expose this assembly-export convention.


## P9-M4-D: candidate indices must be bound to the exact GLB bytes loaded by the browser

A node index is meaningful only inside the exact GLB file from which it was inventoried. A user may select another revision, re-export, or similarly named model; applying the registry's node index to that file can focus an unrelated part while appearing plausible.

Correct pattern:
- Include only the expected GLB SHA-256 and a minimal candidate projection in the API response; do not expose full geometry records, source paths, installation transforms or telemetry metadata.
- Compute SHA-256 over the exact ArrayBuffer selected by the browser before enabling any candidate index action.
- Require strict equality between local file digest and the registered GLB digest. If Web Crypto is unavailable or hashes differ, disable all candidate-location actions.
- Keep manual whole-model browsing available; the digest gate applies specifically to evidence-driven node location.
- A successful digest match only proves file identity. It does not prove the candidate's visual identity, physical installation, coordinate datum or as-built status.
- Reject candidate projections with selector_promoted=true, invalid status, out-of-range indices, duplicate descendants or inconsistent geometry-node counts.

## Distinguish STEP source node counts from GLB scene node counts

The pinned STEP assembly tree and the converted GLB scene are separate representations. The current accepted registry reports 852 GLB scene nodes; do not repeat the STEP-side source-node count as if it were the GLB inventory count. Always read the count from the exact registry artifact being reviewed.
