# P9-M4-I 三设备真实资料准入表

状态：**等待来源资料；未生成真实设备模型**  
用途：收集可追溯资料，供参数化基线与 TRELLIS.2 外观生成进行同输入对比。

## 通用资料要求

每份文件必须保留原始文件，不要先裁切、压缩或重命名覆盖原件。后续登记：
- 原始文件名和 SHA-256；
- 来源 URL / 文档编号 / 版本；
- 页码、图号、表号或图片视角；
- 单位和坐标约定；
- 是否允许用于本项目内部模型生成及展示。

不要求用户必须提供全部资料。没有尺寸的项目保留 unknown，不以 AI 估计值补齐。

## 1. 电池组（component.battery）

优先需要：
- 产品外观照片（正面、侧面、顶部，最好包含尺度参照）；
- 外壳长宽高、安装孔位置/直径；
- 连接器位置与朝向；
- 安装面及固定方式；
- 产品型号、供应商、硬件版本。

可选：
- 电池盒装配图；
- 质量/质心数据（仅记录，不用于反推外形）；
- 结构或热设计手册。

## 2. 反作用轮（component.reaction_wheel）

优先需要：
- 反作用轮产品照片或三视图；
- 外壳包络尺寸；
- 安装孔位、安装面和支架图；
- 旋转轴方向定义；
- 型号、供应商、硬件版本。

可选：
- 转子/外壳剖面图；
- 接口控制文件；
- 质量、惯量和转速范围（作为物理属性证据，不作为外形尺寸替代）。

## 3. 星敏感器（component.star_tracker）

优先需要：
- 产品照片（正面、侧面、顶部）；
- 外壳尺寸；
- 光学窗口和遮光罩尺寸；
- 光轴/boresight 定义；
- 安装面、孔位、安装支架；
- 型号、供应商、硬件版本。

可选：
- 光机结构图；
- 光轴与本体坐标系关系；
- 产品技术手册。

## 公开来源初筛（2026-11-17）

本轮公开检索发现两类可用于对照的真实 CAD 来源。它们是独立参考型号，不自动等同于当前项目的具体设备身份，也不自动获得安装映射批准。

### 电池组

- BIRDS4-CAD（Kyushu Institute of Technology / BIRDS Open Source）公开仓库列出 `B4_battery v1.step`；仓库标示 MIT 许可证。
- OreSat 的 `oresat-batteries` 公开项目说明 v3 电池包于 2021-03-15 随 OreSat0 飞行，机械 CAD 在 Onshape；这是另一套真实飞行硬件参考。
- 初步结论：M4-A 真实证据注册产物已将 `component.battery` 关联到 `BIRDS4_FM_Final v7/B4_battery v1`。它是 reference CAD，不是已验证实装，也尚未具备 GLB 节点渲染资格。电池组应退出“缺失 CAD 生成对象”队列，转入真实 STEP→GLB crosswalk / 视图验收；只有目标硬件型号不一致时才考虑合成补形。

### 反作用轮

- BIRDS4-CAD 公开仓库列出 `B4_RW_Assembly v55.step`，可作为实际小卫星反作用轮装配参考。
- 另有公开 CubeSat reaction-wheel 项目提供 3D 打印硬件文件，但其 GPL-3.0 许可证和教育/原型定位需要单独审查，不能直接当作航天飞行硬件证据。
- 初步结论：M4-A 真实证据注册产物已将 `component.reaction_wheel` 关联到 `BIRDS4_FM_Final v7/B4_RW_Assembly v55`。它同样只是 reference CAD，仍需完成 STEP occurrence→GLB node crosswalk、旋转轴/安装面检查和人工视图验收。反作用轮应退出当前 AI 补 CAD 主生成队列，作为有真实几何 ground truth 的对照组。

### 星敏感器

- 本轮未在 BIRDS4-CAD 文件清单中找到可明确识别的星敏感器 STEP。
- KAIROSPACE KSST-01 产品页列出 56 × 60 × 93 mm、197 g（含遮光罩），并明确列出 2.88 MB CAD model 下载项及 10.68 MB datasheet。当前尚未取得可校验的原始 CAD 文件字节、SHA-256 和再使用许可；产品型号也尚未确认与项目目标星敏感器一致。
- CubeSpace Gen2 CubeStar 页面也列出 CAD Model Request，不能把产品页的尺寸/照片等同于可用的原始 CAD。
- 初步结论：星敏感器仍是唯一需要重点处理的缺 CAD 对象，但不能直接认定没有 CAD。先尝试取得 KSST-01 页面列出的原始 CAD 与许可/来源证明；若文件可取得且目标型号一致，优先走真实 reference CAD 路线。只有文件不可取得、许可不允许使用或目标型号不同，才启动 image-to-3D。若先做生成预览，在取得来源许可和尺寸/接口证据前仅限 G1，不验收 G2。

### 调整后的实验顺序

1. 先从已固定哈希的 BIRDS4 CAD 输入中定位电池与反作用轮 STEP 的实际节点、装配关系和尺寸；用它们作为“有真实 CAD ground truth”的正控制组。
2. 对正控制组从 STEP 渲染固定视角图像，再进行 image-to-3D；比较生成网格与真实 STEP 的包围盒、表面距离、拓扑/接口特征和人工修复时间。
3. 星敏感器作为“CAD 缺失”候选；在取得真实输入资料前，不填充未知尺寸、不声称工程精度。
4. 三类对象均保留来源哈希、许可、模型版本、参数、渲染视角、生成日志和人工评审记录。

资料获取门禁：KAIROSPACE 页面列出 CAD model 2.88 MB，但本轮页面解析只确认到下载条目，未取得可复验的 STEP/IGES/GLB 字节，也未核实文件许可。不得将“页面列出 CAD”写成“已获得 CAD”，也不得将第三方产品尺寸直接当作本项目设备尺寸。

公开来源：
- BIRDS4-CAD：`https://github.com/BIRDSOpenSource/BIRDS4-CAD`
- OreSat battery CAD：`https://github.com/oresat/oresat-batteries`
- CubeSat reaction-wheel prototype：`https://github.com/Harveyn4444/cubesat-reaction-wheel`
- KAIROSPACE KSST-01：`https://satsearch.co/products/kairospace-star-tracker`
- CubeSpace Gen2 CubeStar：`https://satsearch.co/products/cubespace-gen2-cubestar-miniature-star-tracker`

## 资料验收状态

| 对象 | 原始图片 | 尺寸依据 | 接口依据 | 型号/版本 | 当前状态 |
|---|---|---|---|---|---|
| 电池组 | 用户目标设备图片未提供 | M4-A 已注册真实 STEP 候选，尚未完成节点渲染验收 | 需从 STEP/装配证据核验 | BIRDS4 B4_battery v1 | reference_cad_registered_not_as_built |
| 反作用轮 | 用户目标设备图片未提供 | M4-A 已注册真实 STEP 候选，尚未完成节点渲染验收 | 需核验安装面与旋转轴 | BIRDS4 B4_RW_Assembly v55 | reference_cad_registered_not_as_built |
| 星敏感器 | KAIROSPACE 产品页图片候选 | 产品页列出 56×60×93 mm | 产品页列出 CAN/RS485，机械接口未取得 | KAIROSPACE KSST-01 | CAD_listed_2.88MB_bytes_and_license_unverified |

## 生成实验的输入对齐

每个设备有来源资料后，使用同一组输入分别运行：
1. FreeCAD 参数化基线（只使用有来源的尺寸与位置）；
2. TRELLIS.2 Image-to-3D（外观候选）；
3. GLB 审计；
4. 统一视角人工评分；
5. 人工修复时间与计算资源记录。

不得使用不同来源质量的输入比较两种路线。AI 生成结果始终登记为 `synthetic_reference_geometry`，不绑定遥测、不创建实装实例、不进入 as-built 视图。
## 2026-11-19 来源复核补充：KSST-01 与开源替代项

### KAIROSPACE KSST-01
- 官方产品页：`https://www.kairo.space/products/ksst-01-star-tracker`；官方 1 页 datasheet：`https://www.kairo.space/_files/ugd/dca4f9_cdd20d924ed24d76b6ff17cfba81dbaf.pdf`（文件标识 KSDS-ST01-2605-1/1）。
- Datasheet 公开规格：56 × 60 × 92.8 mm（with baffle）、175 g（without cable, MLI and protective covers）、aperture 18.6 mm、focal length 20.44 mm、FOV 22°、update rate 10 Hz。Satsearch 页面另列 197 g（including baffle），与官方 datasheet 的质量口径不同；不得混用。
- 官方 datasheet 页脚写明 Copyright © 2026 KAIROSPACE Company Limited, All rights reserved；官方产品页亦声明未获事先书面许可不得复制/使用网站材料。当前不把产品图、机械图或 PDF 作为 image-to-3D 输入，不把第三方尺寸写入项目目标设备参数。
- Satsearch 页面 `https://satsearch.co/products/kairospace-star-tracker` 的 Downloads 区列出 CAD model (2.88 MB)，但公开页面的图标不是可直接下载的 CAD 文件；页面的 Request details 流程将 CAD model 作为向供应商请求的资料项。尚未取得实际 STEP/IGES/GLB 字节、SHA-256、文件版本和许可。
- 结论：`CAD_listed_bytes_unavailable_license_unverified_target_identity_unconfirmed`。下一步必须取得供应商原始 CAD 与书面许可，或由项目负责人选择许可清晰的替代型号；不因网页存在 CAD 字样而宣称已取得。

### 开源星敏硬件筛查
- OreSat：`https://github.com/oresat/oresat-star-tracker-hardware`，default branch `master`，审查 commit `316fb85958342417e69128ae95b5106b145cec22`。仓库树有 star tracker camera board 的 KiCad PCB/原理图、DXF 和 release ZIP；无 STEP/STL/FCStd/IGES/GLB，也未发现 LICENSE 文件。它是电子板卡资料，不是完整光学头/遮光罩/机械安装 CAD，不能替代 KSST-01 几何。
- SPEL SOST：`https://github.com/spel-uchile/Star_Tracker`，GPL-3.0。README 说明 SOST 在 SUCHAI-2/3 和 PlantSAT 使用，硬件组合为 RPi Zero W 与 RPi V2.1 camera；仓库提供软件和集成照片，没有机械 CAD。它是独立参考设计，不是 KSST-01 同型号设备。

### 图像到 3D 模型可执行性复核
- TripoSR：`https://github.com/VAST-AI-Research/TripoSR`。官方 README 声明 MIT 许可证覆盖代码和预训练模型，单张图默认约 6 GB VRAM，支持 `--model-save-format glb`；`--chunk-size` 可降低显存峰值。当前最适合本地 G1 feasibility pilot，但不代表机械尺寸精确。
- TRELLIS.2：`https://github.com/microsoft/TRELLIS.2`。官方要求 Linux + NVIDIA GPU ≥24 GB + CUDA 12.4 推荐配置；作为远端高质量候选，不作为 RTX 5060 本地默认。
- Stable Fast 3D：`https://github.com/Stability-AI/stable-fast-3d`。约 6 GB VRAM，但模型权重 gated；使用 Stability AI Community License，包含分发通知/署名义务，Windows 支持实验性；当前不作为默认。
- Hunyuan3D-2.1：`https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1`。shape generation 约 10 GB，shape + texture 约 29 GB；Tencent Community License 有地域与分发条件，先完成适用范围审查，不作为默认。

### 当前门禁
- 没有任何许可明确的 KSST-01 原始图像/CAD 输入，因此本轮没有运行 TripoSR、TRELLIS.2 或其他生成模型，也没有生成星敏感器模型。
- 只有输入图来源、SHA-256 和许可范围均核验后，才运行 TripoSR G1；输出只进入 `synthetic_preview`，不得推导安装孔、boresight、接口或 as-built 身份。
- 电池组和反作用轮仍是 BIRDS-4 真实 CAD 对照组；本轮不再对它们重复生成。
## 2026-11-19 新增 NASA 星敏感器视觉来源候选

### Europa Clipper 3D-printed Star Tracker Mock-Up

- NASA Science 页面：`https://science.nasa.gov/missions/europa-clipper/europa-clipper-resources/3d-printed-star-tracker-mock-up/`
- 页面描述该物体为 Europa Clipper stellar reference unit 的 3D 打印 mock-up，包含 optical head 与 shielding，用于 fit/tool-access 检查；下载条目为 2.49 MB JPEG，页面日期 2025-05-29，来源署名 NASA/JPL-Caltech。
- 可直接定位的媒体 URL：`https://assets.science.nasa.gov/content/dam/science/missions/europa-clipper/images/Star-Tracker-3D-Print-Full.JPG`
- NASA Images and Media Usage Guidelines：NASA 内容通常可用于非商业教育/信息用途，范围包括 computer graphical simulations，以及用于 3D model rendition 的媒体文件；第三方受版权保护素材应标明版权持有人。该页面只有 NASA/JPL-Caltech credit，未显示独立的第三方 copyright 标记。
- 初步判定：这是目前最合适的单图 G1 输入候选，可用于“通用星敏光学头/遮光罩外观”预览，不是项目目标设备的型号证明。因为 credit 中含 JPL-Caltech，仍保留 source-specific rights review；在原始 JPEG 字节、SHA-256 与最终许可审查记录进入 manifest 前，不设置 runner 所需的 `license.status=approved`，不运行 TripoSR。
- 该图没有尺寸、安装孔、光轴/boresight、接口和真实尺度信息；生成结果只能是 `synthetic_reference_geometry` / G1，必须标记 `scale_status=unknown_from_single_image`，不得作为 reference CAD、安装坐标或 as-built。
- NASA 的 SI usage guidance 要求生成输出明确标注为 AI 生成，不得使用 NASA 标识，不得暗示 NASA 审查或背书；输出署名生成工具本身，不将模型结果表述为 NASA 提供的几何事实。

### NASA Europa Clipper 3D 模型与 2025D 打印模型（视觉控制候选）

- NASA 官方 Europa Clipper 3D model 页面提供 `clipper_spacecraft.glb`（34.05 MB）和 USDZ（14.61 MB）；该页面称其为 3D graphic model，不是工程 CAD。
- NASA Europa Clipper 2025D scale model 页面列出两个 Stellar-Reference Units，并说明模型约 1:106、尺度并非精确；其页面标注 CC0 / Public Domain Dedication。
- 这两项可以作为“有公开视觉参考”的 control/审查线索，但不是 BIRDS-4、KSST-01 或项目目标星敏的真实 CAD。当前尚未取得文件字节、哈希、节点/部件 crosswalk，故不纳入本轮可交付模型，不做节点拆分或实装映射。
- 如果后续取得文件，应先核对具体资产的 license/credit、原始文件哈希、模型树中 SRU 是否独立成节点，再决定是否作为 G1 visual control；不得从 1:106 打印模型推导工程尺寸。

### 本轮结论

NASA 资料为 TripoSR G1 提供了一个比 KSST-01 厂商照片更有希望的公开候选，但目前只完成来源与政策层面的筛查。**原始媒体字节、hash、source-specific permission review 和真实 GPU inference 均未完成**；因此 benchmark 状态继续保持 `pilot_specification_not_yet_executed`。
## 2026-11-19 NASA 星敏 mock-up 来源权利复核与字节准入

- 重新打开精确 NASA Science 源页面及原始媒体链接，确认页面说明该图是 Europa Clipper stellar reference unit 的 3D-printed mock-up（optical head + shielding），用于 fit/tool-access 检查；发布资源为 2025-05-29 的 JPEG，页面 credit 为 NASA/JPL-Caltech。
- NASA 当前 Images and Media Usage Guidelines 明确把“用于 3D model rendition 的 media files”纳入 NASA 内容范围，并允许教育/信息用途（包括 computer graphical simulations）；页面只显示 NASA/JPL-Caltech credit，没有单独第三方 copyright 标记。该依据支持非商业、教育/信息用途的 G1 派生预览候选，但不等于 NASA/JPL 对本项目输出作出审查或背书。
- 当前只记录 `policy_basis_found_pending_project_owner_signoff`：候选用途限 `model_generation` + `internal_preview`，`external_distribution_allowed=false`。不得使用 NASA/JPL 标识，不得暗示 NASA/JPL/Caltech 背书，输出需标记 AI-generated 并归因 TripoSR。若后续用于商业/推广或对外分发，必须重新审查。
- 图像是单视角现场照片，画面含手/前臂与工作台背景；模型输入必须人工裁切/去背景，只保留星敏 mock-up。不能把照片中的人体/环境作为生成对象。
- 新增 `scripts/cad/fetch_p9m4i_source_image.py`：只从固定的 `assets.science.nasa.gov` 原始 URL 下载，限制重定向主机、MIME、JPEG SOI/EOI、文件大小与 SOF 尺寸；原始 JPEG 与包含 bytes/尺寸/来源/权利审查状态的 `source-intake.json` 原子写入，不覆盖已有证据。
- 新增来源准入测试：校验 JPEG 尺寸解析、SHA-256、错误 MIME、非 JPEG、越权重定向、超限文件和禁止覆盖。CI 增加独立 `nasa-source-intake` job，将原始字节和 intake receipt 作为 artifact 保存。
- 当前原始媒体字节与 SHA-256 仍待最新 CI artifact 实际获取；`source_bytes_obtained=false`、`source_sha256=null`、`eligible_for_inference=false` 保持不变。项目负责人 sign-off、背景分割人工复核、TripoSR 权重本地哈希与 CUDA preflight 完成前，不运行推理。
- 经验：权利政策依据、项目用途批准、原始字节完整性、预处理验收和 GPU 可执行性是五个独立门禁；不能用其中一个 PASS 代替其他门禁。