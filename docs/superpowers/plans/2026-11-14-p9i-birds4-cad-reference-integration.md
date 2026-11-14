# BIRDS-4 CAD reference integration — P9-I3

## Source verification

- Repository: https://github.com/BIRDSOpenSource/BIRDS4-CAD
- Pinned upstream commit: `3bab47a0f53410ef79005d21321ff659726f0a1c`
- Pinned CAD directory tree: `0b84617fd98db32b5d997dc24585da95d5bd2b55`
- CAD inventory: 46 STEP files, each recorded with upstream Git blob SHA-1 and byte size in `birds4-cad-source-manifest.json`.
- Main FM assembly: `BIRDS4_FM_Final v7.step`, 22,964,753 bytes, upstream blob `0a1eea524ad16c9f06961a62bb550cad4d2d9e36`.
- Repository license: MIT, copyright Kyushu Institute of Technology (2021). Preserve the upstream license notice in redistributed derived assets.
- README identifies BIRDS-4 as a 1U (10×10×10 cm) CubeSat programme; satellite names include Tsuru, Guarani-1 and Maya-2.

## Selected conversion targets

The reproducible conversion job will import and export:
1. `BIRDS4_FM_Final v7.step` — complete FM reference assembly.
2. `B4_RW_Assembly v55.step` — reaction-wheel assembly reference.
3. `B4_OBC v3.step` — OBC board reference.
4. `B4_COM_board v19.step` — communications board reference.
5. `B4_battery v1.step` — battery reference.
6. `B4_FM_+X_panel v1.step` — FM +X panel reference.

All six are pinned by exact source blob SHA-1 and size. CI will compute SHA-256 after download, let CadQuery honour any STEP-declared units, independently infer the source scale from the documented 1U full-assembly envelope, export metre-based GLB, and produce an assembly tree / bounding-box / conversion report. If the measured envelope falls outside the explicit 1U plausibility window, conversion stops rather than guessing. The GLB and reports are CI artifacts initially; no large third-party binary is committed to the application source tree in this stage.

## Critical engineering boundary

This is a **BIRDS-4 flight-model CAD reference**, not a claim that the user's Basilisk spacecraft model is BIRDS-4. The reference CAD must remain separately identified until the following are independently reviewed:
- assembly datum and handedness;
- each part's local and parent coordinate frame;
- exact flight-unit applicability (Tsuru / Guarani-1 / Maya-2);
- FM versus EM variant and revision;
- installation transform evidence from the FM assembly procedure and drawings.

The Newsletter Issue 49, page 158, is a datum/orientation reference only. It does not, by itself, establish a complete numeric transform table. BIRDS-3 coordinates must never be applied to BIRDS-4.

## Promotion gates

1. Source bytes match pinned Git blob SHA-1 and declared byte size.
2. CAD kernel imports every selected STEP without error.
3. GLB parses as glTF 2.0, contains non-empty geometry and a non-empty node hierarchy.
4. Assembly tree records original names, parent paths and local transforms; no flattening of the only archival representation.
5. Model envelope is physically plausible against the 1U nominal envelope, while preserving the measured CAD envelope rather than forcibly scaling to 100 mm.
6. Derived GLB is registered with its computed SHA-256 and provenance.
7. No instance is marked `verified` until its source assembly placement has been reviewed against the FM assembly procedure and coordinate drawings.

## References

- CAD: https://github.com/BIRDSOpenSource/BIRDS4-CAD
- FM assembly procedure: https://github.com/BIRDSOpenSource/BIRDS4-ProceduresAndReports/blob/main/Procedures/BIRDS4-FM%20Assembly%20Procedure.docx
- Newsletter Issue 49: https://birds1.birds-project.com/files/BIRDS_Newsletter_Issue_No.49.pdf
- BIRDS-3 reference (separate project): https://github.com/BIRDSOpenSource/BIRDS3-CAD


## P9-I3-A 实际转换验收记录（2026-11-14）

GitHub Actions run #21：`43611131544`，结果 PASS；项目脚本与依赖治理 run #1038：`43611131564`，结果 PASS。

转换产物 artifact：
- Artifact ID：`14120795473`
- 名称：`birds4-fm-cad-reference-43611131544`
- ZIP：15,670,157 bytes
- SHA-256：`5b494816d3bf607cd6051c1130529edec273bea1bc69412e738b55bbbb5156df`
- 内容：6 个 GLB、6 份转换报告、6 份装配树、1 份汇总报告。

整星实测：
- STEP：22,964,753 bytes；Git blob SHA-1 `0a1eea524ad16c9f06961a62bb550cad4d2d9e36`。
- STEP 来源 SHA-256：`f59672d2bfa126474a7ddc5e06d277efe55aca7f8f30cff54648352d7da0f920`。
- 装配树：855 nodes，其中 757 个节点直接含几何。
- GLB：852 nodes、754 meshes，61,009,900 bytes（以 report.json 精确值为准）。
- 转换包围盒：104 × 108.3 × 114.5 mm；这是实际 CAD 几何包围盒，不强行缩放为 100 mm。
- STEP 单位由 CadQuery 导入后的 1U 整星尺度独立核验，采用 mm→m 的 0.001 转换。

重要审查发现：
- 整星 STEP 装配树中出现 `B4_EM_*`、`B3_*` 命名节点，同时也出现明确的 B4 FM 面板与 B4 单机子装配。
- 因此，文件名中的 `FM_Final` 不能作为树内每个子装配均属于同一飞行单元的充分证据。
- 这套 CAD 只能先登记为 BIRDS-4 参考几何；不生成 as-built equipment instances，不把任何节点直接标记为可渲染的 verified 装机单机。
- 下一轮必须对照 FM Assembly Procedure、对应型号（Tsuru / Guarani-1 / Maya-2）的机械图纸与部件版本，审定混合命名子装配的适用性；同时确认 CAD datum、手性和模型轴向。

转换器新增 `assembly-review.json`，统计 FM/EM/B3 命名、重复部件 occurrence 和候选设备类别，并明确输出 `render_eligibility=false`、空装机实例列表。该报告是来源审计，不是人工装配审定。
