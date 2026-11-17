# P9-M4-C：STEP occurrence 到 GLB node 的只读交叉核验

日期：2026-11-17  
基线：P9-M4 统一映射与场景状态面板已实现；参考注册清单仍禁止逐节点渲染。

## 问题

M3 候选保存的是 STEP assembly occurrence path；三维渲染器需要 glTF node index。两者不是同一个命名空间。不能把 STEP 路径直接当作 GLB selector，也不能只凭同名就宣称节点对应关系已经验证。

## 本阶段实现

注册器读取实际 GLB JSON chunk，提取 node index、name、mesh index、child indices 和 parent index。对每个 STEP 候选 occurrence 生成只读 crosswalk candidate：
- exact node-name match；
- duplicate exact-name ambiguity；
- expected STEP parent name 与 GLB parent name consistency；
- unmatched / parent mismatch 分类；
- 每条候选保留源 STEP path 和 GLB node index。

当前只生成 `glb_node_binding_candidate`，正式 `glb_node_binding` 仍为 null。即使名称和父节点都唯一匹配，也必须经过原始 GLB 可视化审查，确认网格外观、局部坐标和父子变换语义后，才允许提升为 reference-CAD-only selector。

## 禁止事项

- 不把候选 selector 当作 as-built 实装身份；
- 不把 STEP world transform 转成安装坐标；
- 不允许 reference CAD 候选绑定实时遥测；
- 不允许候选节点进入 as-built view；
- 不允许名称歧义或父节点不一致时选取第一个节点。

## 验收

1. 从真实 GLB 解析 node inventory，且 GLB 哈希与转换报告一致。
2. 唯一名称 + 父节点一致可生成 candidate-only node index。
3. 重复名称标记 ambiguous，不输出 index。
4. 父节点名称不一致标记 mismatch，不输出 index。
5. 正式 selector 始终为 null，renderable 始终 false。
6. M3 五项审批门禁、零 as-built、零 telemetry binding 保持不变。


## 补充：容器完整性与父链证据门禁

- GLB inventory 不再只读取 JSON chunk：遍历完整 chunk envelope，校验 chunk 边界、4-byte 对齐、JSON 首块、BIN 第二块（存在时）和重复 chunk。
- 校验节点 mesh 引用、child index、multiple-parent、hierarchy cycle 与 scene root。
- STEP 节点名称缺失、父节点路径/名称缺失时，状态必须为 incomplete，不得因“唯一同名”输出候选 index。
- STEP 根节点只可与 GLB 根节点形成候选；STEP 子节点必须与 GLB 父节点名称一致。
- 增加负向回归：missing parent name、root/child mismatch、chunk overrun、node cycle。
- 所有输出仍为 candidate-only；即使候选唯一，也不代表几何、坐标基准或安装身份通过审查。
