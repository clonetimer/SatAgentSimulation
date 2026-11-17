# P9-M3：实装坐标与装配证据审查

日期：2026-11-17  
基线：P9-M2 已合入 main（merge commit `61abd4ce931cfc7a29dec83f9587db6d028f3c08`）

## 目标

对 P9-M2 的三个候选（蓄电池、反作用轮、Perovskite Solar Cells 载荷）建立可追溯的工程证据审查包。M3 将 CAD 源几何变换与真实安装变换严格分离；本阶段可以核验矩阵数学性质和父子变换组合，但不能仅凭 CAD 数值宣称 as-built 安装位置、朝向或飞行单元适用性。

## 当前候选基线

输入来自：
- 固定 CAD 源：BIRDSOpenSource/BIRDS4-CAD commit `3bab47a0f53410ef79005d21321ff659726f0a1c`，tree `0b84617fd98db32b5d997dc24585da95d5bd2b55`。
- 整星 STEP：`BIRDS4_FM_Final v7.step`，Git blob SHA-1 `0a1eea524ad16c9f06961a62bb550cad4d2d9e36`。
- P9-M2 候选审计：Run `44001085568`，artifact `14291767773`。
- P9-I 原始 CAD/装配树：Run `43692477458`，artifact `14153195945`。
- FM Assembly Procedure：固定 commit `d7eadd98f5327e1ddea82b640293d49bfdb09555`，Git blob SHA-1 `4236578c000b413773399d91e094b57a20a52e28`，字节数 61,750,445。
- 坐标方向参考：BIRDS Newsletter Issue 49 第 158 页；只提供导轨/框架轴向说明，不提供整星数值原点和逐单机六自由度安装矩阵。

## 必须独立审查的五类证据

1. **飞行单元身份**：Tsuru、Guarani-1、Maya-2 中的具体对象；不能从项目级 CAD 或文件名推断。
2. **硬件身份**：部件号、型号、硬件版本、FM/EM/B3 适用性及批准图纸修订。
3. **装配关系**：FM Assembly Procedure 中可定位的步骤/条款，父件—子件关系和对应批准装配图。
4. **坐标基准**：整星 datum、原点、轴向、右手/左手约定、单位、源坐标系到整星坐标系的转换依据。
5. **安装变换**：有批准来源的局部安装变换、父子 frame、完整变换链和审查/批准记录。

每项证据记录应包含来源 URI、来源 commit 或文件哈希、文档/图纸编号、修订、页码或步骤/表格定位、支持的具体 claim、审查人和审查状态。只存在文件链接或文件名不算已审查证据。

## CAD 变换数学核验

从原始 855 节点 assembly tree 读取候选节点的 `local_transform_m`、`world_transform_m` 和父节点路径，执行：
- 4×4 齐次矩阵及有限值检查；
- 最后一行 `[0, 0, 0, 1]` 检查；
- 3×3 旋转矩阵正交性检查；
- 行列式约等于 +1，排除反射/错误手性矩阵；
- 对有父节点的 occurrence 检查 `T_world(parent) × T_local(child) = T_world(child)`。

上述通过只证明 CAD 转换数据在数学上自洽，不证明 CAD 原点就是飞行器工程 datum，也不证明该 occurrence 被装配到具体飞行单元。报告字段必须明确写为 `source_CAD_geometry_transform_not_as_built_installation_transform`。

## 程序文件获取与提取

CI 从固定上游 commit 下载 FM Assembly Procedure DOCX，并核对字节数和 Git blob SHA-1。随后从 OOXML 主文档 body 按顺序提取段落和表格文本，保存来源 SHA-256、段落/表格位置索引。

提取结果仅是可定位的原始文本证据：
- 不自动推断零件适用性；
- 不把 OOXML 段落序号伪装成页码；
- 不自动签发装配关系或安装矩阵；
- 未经人工阅读与 claim 级审查，状态保持 `not_reviewed`。

## 交付

- `src/sat_sim/cad_installation_review.py`：变换数学检查与安装证据审查包生成。
- `scripts/cad/build_installation_review_packets.py`：从候选映射、CAD assembly tree 和来源 manifest 生成审查包。
- `scripts/cad/fetch_birds4_procedure.py`：按固定 commit 下载程序文件并校验 Git blob SHA-1/字节数。
- `scripts/cad/extract_birds4_procedure_evidence.py`：按原始顺序提取 DOCX 正文段落/表格和来源收据。
- `tests/cad/test_installation_review.py`：齐次矩阵、正交性、行列式、父子变换、缺失证据和禁止提升测试。
- `tests/cad/test_procedure_extractor.py`：DOCX OOXML 顺序提取和 Git blob 哈希测试。
- `.github/workflows/p9m3-installation-evidence.yml`：下载固定 CAD/候选 artifact、获取并校验 Assembly Procedure、生成三个真实候选审查包并保存证据。

## M3 当前验收边界

- 三个候选均必须有唯一 CAD node path、父节点路径和来源版本。
- CAD local/world 变换必须通过数学检查，父子组合必须一致。
- 三个候选均因整星源树混有 EM/B3 节点而保留 flight-unit identity review。
- Assembly Procedure 可完成字节级来源校验和原始文本提取，但不把“已下载/已提取”当成“已审查/已批准”。
- 坐标基准与安装变换证据未完成 claim 级审查时，三个候选均保持 `blocked_evidence_incomplete`。
- `instance_id=null`、`installation_transform=null`、`spatial_anchor=null`、`render_eligible=false`。
- 不改变 Basilisk 物理模型、TaskSpec、Run Bundle、遥测契约或当前仿真场景。

## 后续

1. 人工审查 Assembly Procedure 提取文本，定位电池、RW、PSC 对应装配步骤和图表。
2. 逐项取得并校验目标飞行单元的批准 BOM、零件图、装配图和 datum/坐标图。
3. 记录每项 claim 的证据定位、冲突、审查人和决定。
4. 只有证据完整、坐标链自洽且审查批准后，才允许向 P9-M4 提交 verified instance mapping。


## Workflow activation

The P9-M3 workflow is registered in the base repository so pull-request path filtering can execute it for this branch. The PR run must validate the merge candidate, not only the feature branch in isolation.


## First source-document locator findings (raw text, not approval)

The hash-verified DOCX body extraction identifies the following review locations:
- Paragraph 21: the document states it guides assembly of the BIRDS-4 **Structural Testing Model**. This limits the claim scope; it is not itself a flight-unit as-built record.
- Paragraph 39 and paragraph 42: axis definition and its figure are present. The figure must be visually reviewed against the coordinate convention; the paragraph locator is not a numeric datum definition.
- Paragraph 106 and Tables 12–14: battery assembly procedure and associated parts/harness/checklist tables.
- Paragraph 113 and Tables 15–17: reaction-wheel assembly procedure and associated parts/fasteners/checklist tables.
- Paragraph 137 and Tables 20–37: -Y panel assembly procedure and associated parts/harness/checklist tables.
- Table 9, checklist item 7 (table row 9, including title and header rows): Perovskite solar cells appears in the adhesive-applied-location checklist; Table 9 also identifies the -Y panel context.
- Table 3 parts-list item 4 lists one Battery; item 8 lists one -Y panel. Table 5 parts-list item 45 lists one brushless motor (reaction wheel).
- Paragraph 495 describes July 2020 rework on BIRDS-4 FM satellites and specifically mentions Maya-2 / Frame 5A. This is rework context for a named unit, not evidence that every assembly item or current CAD transform applies to Maya-2.

The extractor now creates a keyword locator index for battery, reaction wheel, Perovskite Solar Cells, -Y panel, Structural Testing Model, axis definition and named flight units. The review packet associates candidate-relevant paragraph/table/cell locations. These locators are navigation aids only; they do not assert the content is an approved configuration or prove an as-built installation transform.

**Immediate review priorities:** inspect the actual axis-definition figure; visually inspect the battery/RW/PSC assembly illustrations and relevant table rows; determine whether the document's Structural Testing Model configuration corresponds to any of the three flight units; and retrieve the approved flight-unit-specific BOM/drawings before any coordinate promotion.
