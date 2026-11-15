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


## P9-I5-B：真实整星 GLB 浏览器验收与工作区收敛

根据 P9-C Run `43678683691` 的真实 Chromium 截图审查，发现 CAD 模式虽然已显示查看器，但右侧 P9-D 仿真对象配置面板仍占据网格列，导致静态 CAD 视口被压缩。已修正模式切换：CAD 模式隐藏仿真配置面板、将场景布局切换为单列并让 CAD 工作区占满可用宽度；返回轨道模式时恢复原配置面板与原有布局。

测试夹具原先只包含无材质、无显式法线的最小三角形，不能证明实际几何可见。现将 fixture 补充为具有法线和 PBR 材质的真实 GLB，并增加只读渲染诊断（场景节点数、可见 Mesh 数、几何三角形数、渲染调用、GPU 几何/纹理资源计数）。浏览器测试要求至少存在可见 Mesh 和三角形。

进一步将 P9-I 转换工作流扩展为在同一 CI job 内加载刚生成的 `BIRDS4_FM_Final_v7.glb`，通过官方 GLTFLoader 在真实 Chromium 中验收整星模型，而不再只依赖小型 fixture。验收记录模型文件大小、加载耗时、场景节点数、可见 Mesh、三角形数、WebGL 资源计数和浏览器 heap（若浏览器提供），并保存桌面 1440×960 与移动端 390×844 截图。真实整星门槛：文件大于 10 MB、场景节点不少于 500、可见 Mesh 不少于 100、三角形不少于 1000；同时要求页面无控制台/网络错误、无外部网络请求、移动视口无横向溢出。

CI artifact 同时保留 STEP→GLB 转换报告、GLB、装配树、来源审查和真实浏览器性能/截图证据，保留 30 天。

## P9-I5-B 真实整星浏览器验收结果（2026-11-15）

最新已完成工作流：[P9-I run 43692477458](https://github.com/clonetimer/SatAgentSimulation/actions/runs/43692477458)，结果 **PASS**。真实整星浏览器专项 1 passed、0 failed、0 skipped；Playwright 用时 143.977 s，整个 pytest 命令用时 152.256 s。

本次验收使用转换器刚生成的真实 `BIRDS4_FM_Final_v7.glb`，不是三角形 fixture：
- GLB：61,009,900 bytes；SHA-256 `f00cabfc02218a50ef807c06281a7d9e5a1cd93178635476aa153fea1c6b4b12`。
- 浏览器加载耗时：12.336 s。
- Three.js 场景节点：27,278；可见 Mesh：26,425；三角形：3,104,231。
- WebGL render calls：26,427；GPU geometry：15,733；textures：1。
- Chromium reported JS heap：183,682,643 bytes。
- 桌面视口：1440×960；移动视口：390×844，横向溢出检查通过。
- 浏览器控制台错误、页面异常、HTTP 4xx/5xx、请求失败和外部网络请求门禁均通过。
- 转换与浏览器证据 artifact：ID `14153195945`，ZIP 21,118,534 bytes，SHA-256 `ac98ec8ad356c7636271be894bc79b2497446dd416f09a5c98349ad89231ce6a`。

视觉证据审查发现，既有桌面/移动截图是在完成搜索、隔离、视角预设和双击聚焦之后截取的，不能单独代表模型初始总览。已在后续提交 `7f35afdf5ae60d74dcacac4253404673354fda36` 增加“初始整星总览”独立截图与性能记录，避免将局部聚焦画面误作默认视图验收证据。

**性能整改项仍保留：** 26,425 个可见 Mesh 对应 26,427 次 render calls，说明该 GLB 的细粒度网格数量较高。按需渲染已消除静止时的持续 RAF 空转，但并未减少单帧 draw calls。下一步评估不损失装配层级和单件可选性的批处理/LOD/分层加载策略，并以真实浏览器帧耗时、交互延迟和内存数据验证，不以压低网格数替代结构正确性。

**身份边界保持不变：** `promotion_status=reference_cad_imported_not_as_built_verified`；855 个 STEP 源装配节点、852 个 GLB 节点；`render_eligibility=false`，不生成装机实例。混合 EM/B3 命名、具体飞行单元、基准坐标和安装矩阵仍须由正式装配文件审定。


## P9-J：工程数字卫星——物理、装配与几何语义统一

### 产品定义

工程数字卫星不是把一个漂亮的 GLB 放在地球旁边。它必须是三个独立可信数据层的可审计组合：

1. **物理状态层（Physics）：** 来自真实 Basilisk Run / 实时会话的轨道状态、刚体姿态、角速度、执行机构状态和时间信息。每个量保留来源、单位、参考系、仿真时刻及质量状态；真值、测量值、估计值分开展示。
2. **工程对象层（Engineering）：** 来自 `workbench-object-tree.v2` 和 capability/schema 的整星—分系统—部件类别—物理参数—输出参量层级。物理对象、控制功能、轨道环境、外部设备和逻辑对象分开；组件模型类别不等于实装设备实例。
3. **几何资产层（Geometry）：** 来自有固定来源、版本、哈希、坐标系、装配路径和安装证据的 CAD/GLB。几何节点不自行声明物理身份，必须通过显式资产映射与工程对象关联。

### 仿真构型几何与实装几何必须分层

工程数字卫星需要同时表达“当前仿真模型如何配置”和“真实型号如何装配”，但二者不是同一数据：

- **仿真构型几何（simulation configuration geometry）：** 从已校验 TaskSpec / Basilisk 原生配置读取，例如反作用轮 `rWB_B` 安装位置、`gsHat_B` 自旋轴，IMU 的 `sensorPos_B` 与安装 DCM，整星质量、质心和惯量。它准确描述当前仿真模型的配置输入；可用于仿真构型视图、物理参数检查和运行结果关联。必须标注“仿真配置几何”，并绑定 TaskSpec/Run 版本；不得宣称是型号 as-built 装配。
- **实装几何（as-built assembly geometry）：** 仅由经过审查的型号 BOM、CAD 装配、机械接口/安装图及坐标基准形成。必须经过独立的 asset-instance mapping gate 才能进入真实装配视图。
- **参考 CAD（reference geometry）：** 仅用于外形、装配树和来源审查；未完成适用型号/飞行单元和安装基准审定前，不参与仿真构型的物理位置、姿态或遥测联动。

例如，Basilisk 的 `rWB_B` 与 `gsHat_B` 是当前仿真轮组的真实动力学几何参数，可以在 B 系仿真构型视图中按米和单位向量展示；但不能据此认定用户型号中的反作用轮真实安装在该位置。若配置未显式提供安装点，则按实际原生默认值展示其语义（例如 B 系原点），不人为偏移以便看清；可通过分层标签/辅助坐标轴改善可读性，但不能篡改数值。

### 资产映射契约

只有具有完整审定记录的实例才能进入工程装配三维视图。映射记录至少包含：
- `object_id` 与稳定的 `asset_instance_id`；
- 原始 CAD / GLB 资产标识、来源版本和内容哈希；
- 源装配节点路径及 occurrence 标识；
- 父实例、局部安装变换和所用坐标系；
- 坐标系手性、单位、基准方向及变换链；
- 适用型号/飞行单元、FM/EM/修订版本；
- 安装证据引用、审查状态和审查记录。

缺少任何必要证据时，不生成实装几何实例，不推断安装数量，不将部件摆放到“看起来合理”的位置。

### 对象与渲染状态

- **已审定实装实例：** 有明确实例、父装配、安装变换和资产节点映射；允许进入工程装配视图，并可与同一实例的仿真量联动。
- **模型类别：** capability 允许配置/执行，但没有型号 BOM 或实例证据；显示在工程对象树和参数面板，不伪装成已安装硬件。
- **参考 CAD：** 来源可靠但型号/装配/坐标未完成审定；只在独立参考模式浏览，不与当前 Basilisk 姿态/遥测绑定。
- **未定位对象：** 保留对象语义和参数/输出入口；在三维视口显示“未建立空间映射”，不生成位置、朝向或几何。
- **外部/逻辑/环境对象：** 保留在任务环境或功能关系图，不放入卫星实体装配树。

### 界面结构

- 主视口：轨道与姿态、工程装配、参考 CAD 三种模式明确分离；顶部始终显示当前数据身份和模式。
- 左侧：唯一权威工程对象树，层级为整星→分系统→实装实例/模型类别→参数与输出；外部/逻辑对象单独分组。
- 右侧：对象检查器分为物理属性、配置参数、装配关系、坐标变换、资产来源、仿真遥测六组；不存在的字段明确显示“未建立”，不填零、不猜测。
- 底部：仅在存在真实 Run/实时会话时出现时间轴，播放、暂停、步进、采样来源和仿真时间一致。
- 交互：对象树与三维高亮双向同步；未映射对象只更新检查器，不改变相机、不出现虚构位置；已映射对象才允许聚焦、隔离和查看局部坐标轴。
- 配置参数仍复用现有 TaskSpec 校验与运行链；结果指标仍由独立评估工作区消费，不写回物理模型。

### 视觉与性能原则

- 中性工程材质、有限强调色、清晰单位和坐标轴；状态同时使用文字/图标/边框表达，不依赖颜色。
- 整星默认视角突出总体构型；分系统和单机使用渐进展开、局部聚焦和必要的剖切/透明化。
- 大型 CAD 优先分层加载和按需细节；批处理/实例化必须保留稳定的语义 pick ID、树节点路径和隔离能力。不能以合并几何为由丢失单机可选性。
- 以真实浏览器的首次可交互时间、帧时间分布、交互延迟、GPU draw calls、显存/JS heap、模型切换和资源释放作为性能验收，不仅记录文件加载时间。
- 不把 61 MB BIRDS-4 参考 GLB 当作用户当前整星模型；其 `render_eligibility=false` 状态保持，直到来源和安装证据审定。

### 当前已完成与下一批

当前已将 P9-D 对象树选择的物理类别、对象层级、执行能力、空间锚点、实装数量语义传递到卫星场景检查器。场景会明确显示“未建立 CAD/GLB 节点映射”和“未审定，不生成三维位置”，不再只输出一条泛化状态消息。

下一批 P9-J1：
1. 对现有 capability 参数和 Basilisk 模型审计实际存在的部件位置、安装方向、质心/惯量、执行机构轴向及坐标系字段。
2. 将确有来源的几何/物理锚点提升为显式、版本化的空间映射契约；无来源字段继续保持未定位。
3. 选择一条真实可追溯的子系统链路（优先 ADCS/反作用轮）完成“对象树→物理参数→真实模型几何→Run 遥测→选中高亮”的端到端样板。
4. 对真实整星视图做批处理/分层加载实验，保留原始装配树与单机级选择语义。
5. 通过 Ubuntu、Windows、Chromium 桌面/移动和原生 Basilisk 回归后，再扩展其他分系统。


### P9-J1：首个可交互仿真构型视图（ADCS 反作用轮）

已开始实施一个与 as-built CAD 完全分离的“仿真构型”模式：
- 只有 `whole_spacecraft.*` TaskSpec 经后端校验成功后，才向视图传递参数快照；参数编辑、切换到非整星模型后立即清除旧的已校验几何。
- 使用整星原生配置中的 `rw_configuration`、`rw_geometry_mode`、`rw_mount_radius_m`、逐轮 `rw_mount_b_{x,y,z}_m` 和 `rw_axis_b_{x,y,z}`，在 B 系中显示反作用轮安装点与自旋轴。
- 视图以米为位置单位；表格同时显示各轮原始位置与单位轴向。没有显式安装位置时忠实显示 Basilisk 当前默认值（包括位于 B 系原点），不为避免重叠而移动轮体。
- 圆柱/标记只作为单机识别符号，符号尺寸不代表真实轮体尺寸；机身尺寸和外壳几何未获证据，因此该视图不绘制虚构机身包络。
- 未通过校验、配置模式不支持或几何输入不完整时 fail closed，不显示旧配置冒充当前构型。
- 真实 Chromium 专项同时检查未校验配置拒绝、校验通过的四轮金字塔配置展示、行数和模式切换。

此样板证明的是**当前仿真模型的配置几何**，不证明用户型号真实装机位置。下一步需要从同一已校验 TaskSpec/Run 记录增加 IMU 安装位置与传感器安装方向，并将其与轮组几何统一到 B 系检查器；之后才选择具备完整 CAD 来源的一个具体单机做资产节点映射。


### P9-J2～J5：逐步形成完整工程数字卫星

1. **J2 传感器安装与测量方向：** 将 IMU 的 `sensorPos_B`、星敏 B→C 安装旋转、磁强计 B→S 标定/安装旋转、CSS `nHat_B` 接入同一 B 系视图。没有安装位置的传感器只显示方向/姿态符号并标记位置未建立，不擅自放置在机身表面。
2. **J3 整星刚体物理属性：** 检查器展示质量、质心 `r_BcB_B`、惯量 `IHubPntBc_B` 和主惯量方向。惯量可用独立的张量/主轴可视化表达，但不得把惯量椭球冒充真实外壳。
3. **J4 真实运行态：** 从同一 session/stream/sequence 或 sealed Run 原子快照读取 RW 转速、施加力矩/电流、传感器有效性、姿态真值、控制状态和故障事件；动画只由真实遥测驱动。缺帧/乱序/质量异常时冻结对应状态并显示质量标记，不使用合成动画补帧。
4. **J5 型号实装几何：** 完成资产注册表、装机实例和父子坐标变换审定后，再将受控 CAD 节点逐个映射到工程对象树。仿真构型几何、参考 CAD 和 as-built 装配几何始终保留不同身份与数据来源。

每一批只扩展有真实来源的物理量和几何字段；对未知外形尺寸、未给出的安装位置和未审定的父子关系，显示“未建立”，不自动补齐。


### P9-J2-A：IMU 与传感器安装参数检查器

在仿真构型视图中新增传感器参数表：
- IMU：显示经 TaskSpec 校验的 B 系安装位置 `sensorPos_B`，并列出原生 `setBodyToPlatformDCM(yaw,pitch,roll)` 的 B→P 3-2-1 安装角。IMU 只用一个小型位置标记表示传感器基准点，不绘制未经审定的硬件外形。
- 磁强计：显示原生 B→S 3-2-1 安装角；当前 capability 没有给出磁强计安装位置，因此不放置实体标记。
- 太阳敏感器：显示官方 CSS 的 B 系 `nHat_B` 单位法线；当前没有其装机位置参数，因此只展示方向数值，不虚构安装点。
- 目前未将 IMU/磁强计欧拉角转换为三维朝向箭头。需先独立审计 Basilisk 对应 DCM 的主动/被动变换和矩阵方向，避免把 3-2-1 输入角按错误方向画出来。

真实 Chromium 旅程新增检查：传感器表三行、IMU B→P 角、磁强计 B→S 角、CSS 法线，以及校验清除后内容复位。


### P9-J2-B：整星 Hub 物理属性检查器

新增当前已校验整星 TaskSpec 的 Hub 质量与 B 系对角惯量检查器，字段分别对应 `spacecraft_mass_kg` 和 `spacecraft_inertia_{xx,yy,zz}_kg_m2`。只展示实际输入，不从对角惯量推导外壳、质心或惯量椭球；当前模型未提供惯性积和独立质心输入字段，因此明确显示这些表达边界。清除配置时同步清空所有数值。


### IMU 安装方向的 DCM 依据

IMU 三轴显示不再只列 Euler 数值。本轮核对了 Basilisk 上游实现：
- `ImuSensor::setBodyToPlatformDCM(yaw,pitch,roll)` 直接计算 `dcm_PB = eigenM1(roll) * eigenM2(pitch) * eigenM3(yaw)`。
- `eigenM1/eigenM2/eigenM3` 的具体矩阵实现来自 AVSLab/basilisk `src/architecture/utilities/avsEigenSupport.cpp`。
- 为了把平台 P 系三轴画在 B 系视图中，使用 `dcm_BP = transpose(dcm_PB)`；其三列（即 `dcm_PB` 的三行）分别是平台 X/Y/Z 轴在 B 系中的方向。
- Chromium 测试加入 yaw=+90° 的独立基向量检查：P-X→B+Y，P-Y→B−X，P-Z→B+Z。

上游依据：
- https://github.com/AVSLab/basilisk/blob/develop/src/simulation/sensors/imuSensor/imuSensor.cpp
- https://github.com/AVSLab/basilisk/blob/develop/src/architecture/utilities/avsEigenSupport.cpp

箭头长度只是方向识别符号，不代表 IMU 物理尺寸。磁强计同样使用 Basilisk 的 `eigenM1*eigenM2*eigenM3` 组合，但尚未绘制安装方向箭头；先补独立基向量测试，再接入同一坐标显示。


### P9-J2-D：场景模式渲染资源管理

轨道/姿态、仿真构型和参考 CAD 不再同时保持动画循环：
- 切换到仿真构型或参考 CAD 时调用 Globe.gl `pauseAnimation()`，并取消演示轨道 RAF；回到轨道模式时调用 `resumeAnimation()`，仅在演示轨道源存在时恢复位置更新 RAF。
- 离开卫星工作区时同时暂停 Globe.gl 和仿真构型 WebGL renderer；再次进入时按当前保留的场景模式恢复正确渲染器。
- 不清除当前有效 Run/遥测和已校验 TaskSpec；只暂停隐藏视图的渲染，不改变仿真时钟和数据流。
- 对应 Globe.gl 2.46.2 的官方 `pauseAnimation/resumeAnimation` API 已核对。


### P9-J2-E：磁强计与太阳敏感器方向语义

- 磁强计：上游 `Magnetometer::setBodyToSensorDCM` 与 IMU 使用相同的 `M1(roll)·M2(pitch)·M3(yaw)` 构造方式。显示 S 系三轴在 B 系中的方向时，采用 body-to-sensor DCM 的转置方向；新增 90° yaw 基向量检查。
- 太阳敏感器：官方 `CoarseSunSensor.nHat_B` 本身就是 B 系法线向量，不需要 Euler 变换；在三维视图直接按单位向量绘制视场轴，并检查单位长度与默认 -Z 方向。
- 两者都没有经审定的安装位置，因此方向箭头只从 B 系原点发出，作为坐标方向参考；表格明确写“未配置；方向参考自 B 原点”，绝不解释成实际安装点。
- 方向箭头的长度和颜色仅为图形辅助，不代表传感器外形、尺寸或安装距离。


**上游实现核对（Magnetometer）**：固定核对 Basilisk `AVSLab/basilisk` develop 源码提交 `2f8e5d7a8c7ddf55983916cd5640d442ddc99c7c` 的 `src/simulation/sensors/magnetometer/magnetometer.cpp`。其中 `Magnetometer::setBodyToSensorDCM(yaw,pitch,roll)` 明确计算 `dcm_SB = eigenM1(roll) * eigenM2(pitch) * eigenM3(yaw)`。因此 S 系三轴在 B 系中的方向由该矩阵的转置给出。前端已将公共数学实现命名为 `bodyToSensorAxesInBodyFrame`，并通过 IMU 与磁强计各自的语义包装函数调用，避免把磁强计错误标成 IMU。
