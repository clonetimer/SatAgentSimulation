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


### P9-I3-B 装配来源标签审查（由原始 855 节点装配树统计）

对整星树的原始节点标签进行保守筛查（按节点出现次数统计，非独立设备数量）：
- `B4_EM` / EM 命名节点：41。
- `B3_` 命名节点：21。
- 明确 `B4_FM` / `BIRDS4_FM` 命名节点：5。
- 其他未归类节点：788。

其中可见 B4 EM 外板、背板、FAB、MB2、RAB、间隔柱、线圈等名称，也存在 B3 前缀的 GPS 天线、内部板卡和面板名称。该结果证明必须继续做型号/版本适用性审查；但名称本身不能证明它们错误，也不能证明它们实际安装在某一特定飞行单元。

因此当前技术决策：
1. 整星 GLB 可作为 BIRDS-4 CAD 参考几何保留。
2. 不把 855 个 CAD 节点转成 855 台设备；不自动生成装机实例。
3. 不将 EM/B3 命名节点直接删除，也不把它们自动认定为 FM 实装。
4. 在逐项完成型号与装配证据审查前，整个 BIRDS-4 资产的 `render_eligibility=false`；保留现有明确标注的示意模型。
5. 下一批先做来源分类、实例去重与型号适用性审查，再决定哪些子装配可进入真实装机树。


## P9-I3-C：FM 外侧板覆盖扩展

首批转换目标扩展为 9 个：整星总装、RW、OBC、COM、battery，以及全部四块 FM 外侧板（+X、-X、+Y、-Y）。

将 -X 面板纳入单独转换，是因为公开的 BIRDS-4 天线研究确认该外侧板参与 Loop Hentenna 结构；它不是可仅凭外观归类为太阳能板的普通外板。四块面板在资产目录中仍按原始 FM 文件版本分别登记，不混入 EM 或 B3 面板。

四块 FM 面板 GLB 仅作为独立参考资产。只有在逐一确认其父装配、源坐标、飞行单元适用性和机械安装证据后，才允许生成真实装机实例。


## 坐标与尺寸证据补充

BIRDS Project Newsletter Issue 42 第 25 页给出的标准 1U CubeSat 主框架尺寸为 100 × 100 × 113.5 mm。Newsletter Issue 49 第 158 页说明 BIRDS-4 结构导轨沿 Z 轴、框架位于 XY 平面，并描述背板与框架的装配关系。后者只支持轴向/结构方向判断，不定义整星数值原点，也不提供完整单机安装矩阵。

因此转换器的单位识别已收紧为三轴同时满足 80–150 mm（或 0.08–0.15 m）才接受，拒绝仅凭最大轴长度推断单位。当前整星实测包围盒 104 × 108.3 × 114.5 mm 落在该尺度门限内。该结果只用于 mm/m 单位识别，不作为装配坐标或外形尺寸验收。


## P9-I5：数字场景视觉与交互设计门禁

目标：把数字场景从“地球 + 卫星动画 + 参数侧栏”升级为工程可用的三维检查工作区，同时保持真实物理状态与参考 CAD 的身份边界。

### 视觉布局
- 采用沉浸式三维主视口，减少外围边框和重复卡片；顶部仅保留场景名称、数据身份、视图模式和主要动作。
- 左侧对象树按整星/分系统/单机分层，支持搜索、展开折叠、可见性开关；节点显示“真实资产 / 参考 CAD / 示意模型 / 未定位”状态，不用颜色单独表达状态。
- 右侧检查器按“对象信息、几何与来源、装配关系、坐标变换、仿真遥测”分组；无证据字段显示未建立，不显示虚构坐标。
- 底部时间轴仅在实时或历史 Run 数据存在时出现，支持播放/暂停、拖动、步进、当前采样时间和采样来源；CAD 静态预览不显示仿真时间轴。
- 采用低干扰深色工程视口、柔和背景、明确的选中高亮和单位统一的尺寸标注；模型本体保持中性材质，功能类别用细线标记而非高饱和涂色。

### 交互行为
- 鼠标拖动旋转、滚轮缩放、右键/修饰键平移；提供适配触屏的单指旋转、双指缩放和平移。
- 视图预设：整星、+X/-X、+Y/-Y、+Z/-Z、等轴测；“适配模型”根据包围盒调整相机，不硬编码相机距离。
- 单击选择、双击聚焦、隔离显示、隐藏/恢复、透明化父结构、显示坐标轴和测量距离。
- 对象树选择与三维高亮双向同步；选择对象时相机只做有限平滑过渡，不夺取用户自由相机控制。
- 静态 CAD 参考模式与 Basilisk 仿真模式采用明确的模式切换，禁止把 BIRDS-4 模型直接套用当前仿真遥测；只有经审定的 frame/instance 映射才允许联动。

### 性能与可访问性
- GLB 使用实例化/LOD/按需加载；首次只加载整星主视图，单机高精模型按需加载。
- 加载进度、文件来源、哈希校验、失败原因和取消操作可见；模型加载失败时保留原场景，不出现空白页。
- 支持键盘选择、可见性切换、焦点管理和 aria 状态；窄屏下对象树/检查器改为抽屉，不压缩三维视口到不可操作尺寸。
- 不在普通用户界面显示开发调试信息；诊断细节进入可复制的证据面板。

### 验收
- 桌面 1440×900、窄屏 390×844 实测；视口无溢出、按钮不遮挡模型、文字不覆盖关键几何。
- 整星/单机切换、搜索、选择、高亮、隔离、隐藏恢复、六向视图、适配模型、测量和键盘交互全部可操作。
- 真实 CAD 参考模型与示意模型/仿真模型身份标签始终正确。
- GLB 加载/哈希失败、无锚点、坐标缺失时 fail closed；不渲染伪造装机位置。
- P9-C～P9-H 回归通过，且真实 Chromium 截图和交互证据入包。
