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
