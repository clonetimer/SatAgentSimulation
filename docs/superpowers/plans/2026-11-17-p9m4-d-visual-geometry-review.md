# P9-M4-D：工程几何候选可视化审查工作区

日期：2026-11-17

## 目标

将 P9-M4-C2 产生的真实 STEP occurrence → GLB assembly-group → mesh-descendant 候选，接入现有 BIRDS-4 Reference CAD 浏览器，形成可追溯、可人工检查、不可自动批准的几何审查流程。

## 输入事实与范围

- 当前真实注册产物：GLB scene node inventory 为 852 nodes / 1 scene。
- STEP 侧源装配树的计数与 GLB 导出后的 node inventory 属于不同命名空间，不得混为同一个数量。
- 当前三个候选：battery group node 545（14 个 mesh descendants）、reaction-wheel group node 560（24 个）、payload group node 759（1 个）。
- 所有正式 glb_node_binding 仍为 null；selector promotion、renderable、as-built、telemetry binding 均为 0。

## 实施设计

1. API 只返回经过 bundle SHA-256 与 mapping contract 校验的最小审查投影：GLB SHA-256、GLB node count、reference candidate 的 group node index/name、mesh descendant node index/name/mesh index、候选状态和人工审查标志。不返回原始 geometry_asset、安装变换、证据文件路径或遥测绑定。
2. API 必须拒绝 selector_promoted=true、错误候选状态、越界 node index、重复 descendant index、无效 mesh index、几何节点数不一致等记录。
3. 浏览器本地读取用户选择的 GLB，在 Web Crypto 可用时计算完整文件 SHA-256。只有本地 GLB digest 与 API 证据 digest 完全一致，才启用“定位候选组”。
4. 定位动作只在已加载 Reference CAD 场景中选择并聚焦对应 group node；必须通过 GLTFLoader 的 `gltf.parser.associations` 取得 GLB 原始 `nodes[]` index，不能使用 Three.js traversal ordinal；不隔离/隐藏其他节点，不自动修改 GLB，不创建 formal selector，不进入仿真实装视图。
5. 候选卡片显示设备名称、组节点 index、几何节点数量和 descendant mesh 清单。点击定位后复用既有 inspector，显示装配路径、包围盒、局部 TRS 和根坐标 TRS。
6. 缺少 Web Crypto、digest 不匹配、node index 不存在、候选证据不完整时均 fail closed；仍允许用户手动浏览整个参考 CAD，但禁止依据不匹配的证据索引定位。

## 验收

- API endpoint 单测：unlocated-only bundle 保持兼容；reference candidate 返回最小审查投影；promotion、digest、index、descendant 不一致全部拒绝。
- 前端契约：审查面板存在；客户端 SHA-256 必须先于 candidate focus；候选按钮只有 digest match 后可用。
- 跨平台：Ubuntu 24.04 / Windows 2022 契约测试与 JS 语法检查。
- Chromium 浏览器回归：使用带 scene wrapper 与 assembly group 的真实 GLB fixture，先加载错误 revision 验证定位按钮保持禁用，再加载 SHA 一致的 GLB，确认只定位原始 glTF node index 指向的 group，并检查 inspector 的原始索引。后续真实 BIRDS-4 产物验收仍需逐一定位 battery / reaction wheel / payload 并保存截图。

## 明确不在本阶段

- 不将 Reference CAD 转为 As-built。
- 不自动认可候选与真实硬件外形一致。
- 不从 GLB 坐标推导航天器 B 系安装变换。
- 不把 CAD node index 绑定实时 Basilisk 遥测。
- 不以 AI 生成模型替代缺失的工程尺寸、接口或安装证据。


## 真实 BIRDS-4 Chromium 集成验收

P9-M4 unified mapping workflow now has a downstream `reference-cad-browser-review` job:
- It downloads the registry produced by the same workflow run and the independently pinned P9-I full-FM GLB artifact.
- Before launching Chromium it verifies the registry deployment receipt SHA-256 and the exact GLB SHA-256 against `intake.glb_sha256`.
- It loads the real 61 MB BIRDS-4 full assembly in the browser and locates battery, reaction-wheel and payload group nodes by the original GLB node indices.
- It verifies the expected 14 / 24 / 1 mesh descendants, displays original node index and transform inspector, and saves screenshot plus a machine-readable review summary.
- A wrong-revision GLB is loaded first and must keep every candidate-location action disabled.


## 用户可理解的五项审查清单

审查面板新增明确的五项状态，避免把“哈希一致”误解成“设备验收通过”：
1. 文件版本一致性：本地 GLB 与证据清单 SHA-256 是否一致。
2. 装配组与网格清单：候选节点和后代网格是否已读取。
3. 三维外形与遮挡关系：待工程人员目视核对。
4. 局部坐标与安装基准：待核对，GLB 根坐标不等于航天器 B 系安装变换。
5. 实装身份与遥测关联：本阶段不批准、不建立绑定。

面板不提供“批准实装”按钮。完成候选定位只代表审查对象可被检查，正式映射仍需独立工程签核。


## P9-M4-D software acceptance evidence (2026-11-17)

Dedicated workflow run `44098366231` completed successfully:
- Ubuntu 24.04 mapping contract: PASS.
- Windows 2022 mapping contract: PASS.
- Real M3-to-M4 reference registry generation and receipt: PASS.
- Real BIRDS-4 GLB digest-bound Chromium review: PASS (both the digest-mismatch fixture and the full real assembly review).

The exact pinned GLB reviewed by Chromium:
- SHA-256: `f00cabfc02218a50ef807c06281a7d9e5a1cd93178635476aa153fea1c6b4b12`
- 852 GLB scene nodes, 754 meshes.
- Battery: original GLB node #545, `B4_battery v1`, 14 descendant meshes.
- Reaction-wheel assembly: original GLB node #560, `B4_RW_Assembly_v55`, 24 descendant meshes.
- Payload candidate: original GLB node #759, `Perovskite_Solar_Cells_v3`, 1 mesh.

Evidence artifact `p9m4-d-real-birds4-geometry-review` contains the machine-readable summary, browser test report, an overall desktop capture, and one focused Chromium capture per candidate. The test verified that all three registered node indices resolve to the intended nodes in the exact SHA-matched GLB.

**Acceptance boundary:** P9-M4-D's reference-geometry browser workflow is complete. This does not constitute final engineering/as-built approval. In particular, the payload candidate's identity and all spacecraft-B installation transforms remain subject to engineering review. Selector promotion, as-built verification and telemetry binding remain zero.
