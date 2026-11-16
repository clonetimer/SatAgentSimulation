# P9-M：参考 CAD 装配可审查性与证据约束的实例映射

## 目标

将 P9-I 的 BIRDS-4 参考 CAD 从“可旋转、可搜索的静态模型”提升为“可逐节点审查的参考装配”。本阶段先建立可读的 occurrence hierarchy、来源标签、局部/世界变换和几何范围检查，再逐个引入经批准的装配证据。

当前 BIRDS-4 FM STEP 审计仍报告：
- `identity_status=flight_unit_not_identified_from_cad_tree`
- `coordinate_status=datum_and_axis_mapping_not_independently_reviewed`
- `installation_status=as_built_installation_not_verified`
- `render_eligibility=false`
- `generated_equipment_instances=[]`

因此，P9-M 的第一阶段不得生成 verified as-built instances，也不得把来源标签匹配数显示为卫星实际装机数量。

## M1：CAD occurrence 检查器（本批）

- 节点选择显示完整父子层级路径。
- 显示局部变换（父节点坐标）和世界变换（GLB 参考装配根坐标）。
- 显示包围盒尺寸（m）和所选子树 Mesh 数。
- 明确提示：GLB 参考装配根坐标尚未与航天器 B 系/安装基准审定关联。
- 不把 GLB world transform 直接转换成 Basilisk B 系安装锚点。

## M2：源节点与设备类别候选映射

- 基于 source label、父子路径和受控文档建立候选分类。
- 候选状态只能是 `candidate_unverified` / `reviewed`，不得自动成为 `verified`。
- 对 BIRDS-4 FM/EM/B3 混合命名、重复 occurrence、Hentenna 面板等特殊情况保留独立审核项。
- 候选数量只用于来源审查，不作为装机清单数量。

## M3：安装坐标审定

每个可提升为 as-built 的实例必须具备：
1. 明确的 flight-unit / configuration identity；
2. 已批准的装配图或机械接口文件；
3. 父安装基准和设备局部原点；
4. 平移单位、轴向、旋转约定及完整 SE(3) 变换；
5. 独立基向量/尺寸核验；
6. asset hash、source revision 和 reviewer 记录。

缺任一项时，保持无空间锚点。

## M4：与数字卫星场景的安全关联

- 参考 CAD 模式继续作为独立查看模式。
- 仿真物理构型继续由已校验 TaskSpec 驱动。
- 只有经审核的 verified equipment instance 才能在 as-built 装配层显示。
- 参考 CAD 和仿真构型之间可以提供候选对照，但不得自动叠加或复用未经审定的安装变换。
- 对象树选择、CAD occurrence 和 Run 结果只有在 instance ID 明确后才可形成一一关联。

## 验收

- 节点路径和局部/世界变换可读，单位和坐标语义明确。
- 参考模型状态与 as-built 状态有显式区分。
- 混合变体、重复名称和未审定坐标不会被升级成真实装机实例。
- P9-C 场景交互、P9-I CAD 转换、P9-D 对象配置和 P9-F 实时遥测回归通过。
- 任何坐标转换必须由独立几何/基向量证据验证，不能只凭视觉重合。

