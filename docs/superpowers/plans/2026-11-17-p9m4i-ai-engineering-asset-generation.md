# P9-M4-I：AI-assisted Engineering Asset Generation

日期：2026-11-17  
基线：P9-M4-A（PR #159）和 P9-M4-C（PR #160）仍处于 Open；#160 以 #159 为 base，均未合入 main。本实验分支 #161 独立以 main 为 base。

## 目标

验证生成式 3D 与参数化 CAD 能否经济地补全缺少原始 CAD 映射的设备外观，并将结果安全接入 SatAgentSimulation。第一轮保留三个代表对象作对照，但 AI 补 CAD 主目标收敛为星敏感器：M4-A 已将电池组和反作用轮登记为真实 BIRDS-4 reference CAD；二者转为 ground-truth controls，不再重复生成。

本阶段交付的是资产生产与质量审计工具链，不把 AI 生成模型作为运行时依赖，不生成 as-built 实装证据，不将合成几何提升为 reference CAD。

## 技术决策

### 双引擎，不押注单一生成模型

A. 参数化工程几何（主路线）
- GPT/VLM 只负责把资料解析为带来源定位的参数与约束。
- 通过 FreeCAD Python API / OpenCascade 构造确定性几何。
- 已知尺寸必须来自可追溯来源；未知尺寸标记 unknown，不允许由模型补猜后伪装成测量值。
- 适合电池外壳、反作用轮包络/安装接口、星敏外壳与遮光罩。

B. 图像到 3D（外观补充路线）
- 首轮候选：Microsoft TRELLIS.2。
- 备选对照：Tencent Hunyuan3D-2.1，但须单独完成许可审查。
- 输出只作为 synthetic reference geometry；不能作为尺寸、安装孔、内部结构或坐标的权威来源。

### 模型与许可边界

- TRELLIS.2 官方仓库标示 MIT，4B 图像到 3D，官方测试环境为 Linux、NVIDIA GPU ≥24GB、CUDA 12.4；H100 性能数据不能外推到 RTX 5060。
- Hunyuan3D-2.1 使用 Tencent Community License，不等同于 MIT；部署、分发、地区和商业使用限制必须由法务/项目负责人确认后再纳入生产路线。
- 第一轮不把任一生成模型权重打包进 SatAgentSimulation。

## 三个试验对象

| 对象 | 当前 CAD 状态 | 本轮角色 | 主要验证 |
|---|---|---|---|
| 电池组 | BIRDS4 `B4_battery v1.step`，M4-A reference_cad | 真实几何对照组 | STEP→GLB 节点映射、包络与装配证据 |
| 反作用轮 | BIRDS4 `B4_RW_Assembly v55.step`，M4-A reference_cad | 真实几何对照组 | STEP→GLB 节点映射、旋转轴和安装面 |
| 星敏感器 | KSST-01 产品页列出 2.88 MB CAD 下载项；原始字节/许可尚未核验 | 唯一未解决对象；先取真实 CAD，失败后才做 AI 生成 | G1 外观、光轴/窗口的未知边界、来源可追溯性 |

若缺少有来源的尺寸图，参数化模型只允许使用明确标记的展示占位参数；不能宣称 G2。

## G1/G2 质量等级

G1 synthetic visual:
- GLB 结构有效、网格非空、尺寸单位和尺度声明明确；
- 包围盒、网格数量、三角形数、材质数可审计；
- 资产明确标注 synthetic，不具备工程安装精度；
- 人工视觉验收后才允许进入 synthetic 视图。

G2 engineering reference:
- G1 全部通过；
- 尺寸、接口、安装孔、光轴/旋转轴均有来源证据；
- 可由参数化 CAD 重建或独立检查；
- 几何偏差在项目针对具体部件定义的容差内；
- 工程审核签字后才允许登记为 engineering reference，仍不等于 as-built。

G3 as-built:
- 不由 AI 生成达成；仍须 P9-M3 五类真实工程证据和具体飞行单元审批。

## 第一轮实验设计

1. 固定 M4-A 的电池组/反作用轮真实 STEP，导出可复现多视角图像，作为 image-to-3D 正控制组；原 STEP 是几何 ground truth。
2. 对星敏感器取得来源许可明确的多视图图片、尺寸与接口依据；未取得前只允许 G1 占位预览，不允许 G2。
3. 在隔离 Linux GPU worker 上运行 TRELLIS.2；记录模型 commit、checkpoint、配置、耗时和资源峰值。
4. 输出统一为 GLB；保留原始 STEP/FCStd（若有）、源图、生成日志和哈希。
5. 运行本仓库的无第三方依赖 GLB 审计器。
6. 以电池组/反作用轮真实 STEP 做对照，比较包围盒偏差、对称表面距离、网格完整性、尺度一致性、人工修复时间、生成耗时和成本。

## 自动质量审计

- GLB magic/version/chunk 边界与 JSON 解析；
- scene/node/mesh/accessor 引用有效性；
- 网格非空、POSITION accessor 存在、索引数量有效；
- 从 accessor min/max 汇总包围盒；
- 尺度与声明单位一致，拒绝零尺寸/非有限数；
- 材质、纹理和三角形统计；
- SHA-256 与输入/输出 provenance；
- 缺失数据不推断，失败不生成可入库状态。

## 资产身份

新增映射类型 `synthetic_reference_geometry`，与 `reference_cad`、`simulation_configuration_geometry`、`as_built_verified`、`unlocated` 区分。它仅可进入 `synthetic_preview` 视图，不允许 telemetry binding、spatial anchor、as-built eligibility 或 flight-unit claim。

## 预算与成功标准

第一轮技术试验规划 5–8 人日；该数字为工程计划估算，不是模型服务商报价。先完成两个真实 CAD 对照组的视图与几何基准，再对唯一缺失目标星敏感器进行生成试验；只有 G1 审计通过且人工修复成本优于手工建模基线，才扩大到其他未映射类别。

## 本阶段交付

- 合成几何资产 schema 与注册验证器；
- 无第三方依赖 GLB 质量审计 CLI；
- 三设备 benchmark manifest；
- 许可、硬件、模型隔离和质量门禁文档；
- 单元测试与 CI；
- 不修改 Basilisk 仿真模型，不新增核心运行时 AI 依赖。


## 星敏感器 CAD 获取优先级

KAIROSPACE KSST-01 产品页列出 2.88 MB CAD model 与 10.68 MB datasheet，尺寸为 56 × 60 × 93 mm。当前只确认页面上的资源条目，尚未取得可校验的 CAD 字节和再使用许可，也未确认该型号就是项目目标设备。因此第一步是取得原始文件、哈希、来源和许可；只有无法取得、不可合法使用或型号不匹配时，才启动图像到 3D。第三方产品规格不得自动写入本项目目标设备参数。

## 公开技术调研依据（2026-11-17）

### TRELLIS.2
- 官方仓库：`https://github.com/microsoft/TRELLIS.2`
- 官方项目页：`https://microsoft.github.io/TRELLIS.2/`
- 4B image-to-3D，输出 PBR textured mesh，支持 512³–1536³。
- 官方安装说明要求 Linux、NVIDIA GPU ≥24GB、推荐 CUDA 12.4；官方性能数据基于 H100，不能外推到笔记本 RTX 5060。
- 仓库主许可证标示 MIT；第三方依赖仍需分别核对许可证。
- 适配结论：作为隔离 GPU worker 的 G1 外观生成候选；不作为参数化工程几何来源。

### Hunyuan3D-2.1
- 官方仓库：`https://github.com/Tencent-Hunyuan/Hunyuan3D-2.1`
- 官方 API 示例支持异步生成、状态查询及 GLB/OBJ 输出。
- 模型采用 Tencent Hunyuan 3D 2.1 Community License，而非 MIT；许可存在地域、分发和使用条件，且第三方依赖有独立许可。
- 适配结论：只作为可选对照，完成许可证/地区/分发审查前不纳入默认路线。

### 工业机械重建
- DOGREAT3D，Journal of Manufacturing Systems，2026，DOI: `10.1016/j.jmsy.2026.03.024`。
- 论文摘要报告单图机械零件重建、置信度感知多模态融合；F-score@1mm 为 0.526，Chamfer Distance 相对改善 14.1%。
- 适配结论：证明机械重建研究正在进步，但该指标不足以证明卫星单机接口达到 1mm 工程验收；作为研究对照，不直接依赖其模型。

### 参数化 CAD
- 本项目优先使用 FreeCAD Python API / OpenCascade 做确定性参数化几何。
- GPT/VLM 仅产出有来源的结构化参数建议；所有数值保留来源定位和置信/未知状态。
- CAD 内核负责执行与导出，独立审计器负责验证 GLB 结构；不把 LLM 文本输出直接当成几何事实。

## 当前技术选择
第一轮只实施 TRELLIS.2 作为可选 G1 生成 worker，参数化 CAD 作为工程主基线。Hunyuan3D 先不部署，直到许可评审完成。任何生成模型的输出都进入 `synthetic_reference_geometry`，不得隐式转换为 P9-M4 的 `reference_cad` 或 `as_built_verified`。


## 实施进展：参数化基线先行

已新增：
- `p9m4i-parametric-input.v1` 输入 schema：每个尺寸都必须带数值、原始来源定位和来源 SHA-256。
- FreeCAD macro `scripts/cad/build_parametric_synthetic_asset.FCMacro`：按显式 box/cylinder 特征构建 FCStd 和 STEP；不含任何预设卫星单机尺寸。
- 位置坐标也必须带独立来源定位和 SHA-256；缺少任一项即拒绝构建。
- CI 对宏的源约束拒绝逻辑进行静态回归检查。

该宏当前是确定性几何基线，不是三个设备的最终工程模型。尚未提供真实设备尺寸图/安装接口图，因此没有生成或伪造任何具体尺寸。实际 FreeCAD 几何执行需在安装 FreeCAD 的隔离环境完成，随后再导出/转换 GLB 并用独立审计器检查。


## GLB audit hardening and source intake

The GLB auditor now also checks primitive mode, material references, index accessor scalar/component types, triangle index divisibility and non-indexed triangle vertex divisibility. These checks are structural validation only, not proof of geometric accuracy. It additionally requires the embedded GLB BIN payload, validates bufferView/accessor byte ranges, reads the actual float32 POSITION records, and checks that declared accessor bounds agree with the decoded vertex payload. A regression fixture deliberately corrupts vertex data while leaving metadata unchanged.

A three-device source-intake worksheet has been added. Battery and reaction wheel now have real BIRDS-4 reference CAD registrations but are not node-renderable or as-built verified. Star-tracker source CAD has not been obtained and remains blocked pending an auditable source package. No device-specific dimensions or models have been fabricated.


## GLB binary-backed accessor audit hardening

The GLB audit now validates the actual embedded BIN payload rather than trusting only JSON metadata:
- require the embedded GLB buffer and validate bufferView bounds;
- validate accessor byte ranges and combined bufferView/accessor alignment;
- decode float32 POSITION records and compare their actual extrema with declared accessor min/max;
- reject sparse POSITION/index accessors until sparse decoding is implemented;
- reject byteStride on index accessors and validate every index against the POSITION vertex count.

This remains a bounded structural audit, not a replacement for the Khronos glTF Validator or an engineering geometry acceptance review. The audit deliberately fails closed on unsupported accessor forms rather than silently interpreting incomplete data.
## 2026-11-19：KSST-01 来源/许可复核与本地模型选型修订

### KSST-01 CAD 获取结果：仍未获得可用原件
- KAIROSPACE 官方产品页 `https://www.kairo.space/products/ksst-01-star-tracker` 提供公开规格，并链接 1 页官方 datasheet `https://www.kairo.space/_files/ugd/dca4f9_cdd20d924ed24d76b6ff17cfba81dbaf.pdf`。官方 datasheet 给出 56 × 60 × 92.8 mm、175 g（不含线缆、MLI 和保护盖）、18.6 mm aperture、20.44 mm focal length、22° FOV、10 Hz 更新率等参数。
- 官方页面明确声明材料不得在未取得 KAIROSPACE 事先书面许可的情况下复制、分发、传输、缓存或以其他方式使用；datasheet 页脚也标明 All rights reserved。不得将官方产品照片/机械图直接送入生成模型或随项目资产再分发，除非取得书面授权。
- satsearch 产品页仍列出 2.88 MB CAD model，但下载入口实际为资源图标/供应商请求流程，不是可直接获取的 STEP 字节；请求表单明确把 CAD model 作为向供应商提出的资料请求。当前没有 CAD 原件、SHA-256、文件格式或再使用许可。
- 因此 KSST-01 状态维持 `CAD_listed_bytes_unavailable_license_unverified_target_identity_unconfirmed`。公开 datasheet 的规格事实只作为采购/身份核验线索，不作为项目目标星敏尺寸或可复用几何输入；没有模型生成。

### 开源替代资料筛查
- `oresat/oresat-star-tracker-hardware`（default branch `master`，审查 commit `316fb85958342417e69128ae95b5106b145cec22`）含 KiCad PCB/原理图和若干 DXF/板卡 release ZIP，但仓库树没有 STEP/STL/FCStd/IGES/GLB，也未发现 LICENSE 文件。它不能作为完整星敏机械外壳 CAD，且不能在未确认许可前复制其文件。
- `spel-uchile/Star_Tracker` 使用 GPL-3.0，公开内容是 SOST 软件、样例数据和集成照片；仓库文件清单没有机械 CAD。其 RPi Zero W + RPi V2.1 camera 集成方案也不是 KSST-01 的同型号替代物。

### 图像到 3D 模型重新排序
- **本地 G1 首选改为 TripoSR**：官方仓库 `https://github.com/VAST-AI-Research/TripoSR` 的代码与预训练权重均为 MIT；官方 README 说明单图默认约 6 GB VRAM，支持 GLB 输出，`--chunk-size` 可降低显存峰值（代价是更慢）。这比 TRELLIS.2 的官方 ≥24 GB 显存门槛更适合先在本地 NVIDIA GPU 上做受控 G1 试验。
- **TRELLIS.2 保留为高质量远端候选**：官方 README 要求 Linux、NVIDIA GPU ≥24 GB、推荐 CUDA 12.4；不作为 RTX 5060 本地默认执行路线。
- **Stable Fast 3D 暂不作为默认**：虽报告约 6 GB VRAM，但模型权重需要 Hugging Face gated access，许可证是 Stability AI Community License，包含分发署名/Powered by 等义务；Windows 支持仍标为 experimental。
- **Hunyuan3D-2.1 暂不作为默认**：官方 README 报告 shape generation 约 10 GB VRAM，shape + texture 总计约 29 GB；使用 Tencent Hunyuan 3D 2.1 Community License，包含地域、分发及商业使用限制，需先做适用地域和组织用途审查。
- TripoSR 仍只用于 G1 外观预览，不是精密机械重建器；单张图无法可靠恢复遮挡面、安装孔、内部结构、光轴/接口位置或真实尺度。必须使用经许可的原始输入图，并由来源/许可清单、GLB audit 和人工视觉审查共同准入。

### 当前执行门禁与下一步
1. 先取得 KSST-01 CAD 原件及供应商对本项目内部模型生成、预览展示和交付的书面许可；若无法取得，需由项目负责人明确选择一个许可清晰的开源替代型号。
2. 输入图必须是用户提供、供应商明确许可，或许可明确允许衍生模型生成的公开资料；保留原始字节、SHA-256、来源定位和许可文本。未通过许可门禁时，TripoSR runner 必须拒绝执行。
3. 有许可输入后，先以 TripoSR 生成 G1 GLB；用本仓库 GLB binary audit 检查结构、POSITION 数据、bounds 和 SHA-256，再进行多视角人工检查。未取得源尺寸/接口图前，不登记 G2。
4. 电池组和反作用轮继续作为 BIRDS-4 真实 STEP ground-truth controls；不再将它们计入缺失 CAD 的 AI 生成对象。

经验：产品页出现 CAD 下载标记不等于原始 CAD 已可下载，更不等于拥有再使用权；开源星敏软件/PCB 也不等于完整机械 CAD。模型选型必须同时核对显存、输出格式、Windows/Linux 支持和模型/权重许可证。

## 2026-11-19：公开 CI 原始来源制品策略修正

本仓库为 public，GitHub Actions artifacts 对仓库读取者可下载。此前 NASA 来源采集 job 将原始 JPEG 与 receipt 一起上传，和该来源的 `external_distribution_allowed=false`、`project_owner_signoff=pending` 状态冲突。

- CLI 增加 `--receipt-only`：CI 只在内存中读取源图并校验 MIME、JPEG 结构、尺寸和 SHA-256，不将原始图写入 runner 工作目录。
- 公开 artifact 仅上传 `source-intake.json`，记录来源 URI、credit、字节数、尺寸、SHA-256、权利状态和 `inference_eligible=false`；保留期 7 天。
- 两个历史公开原图 artifact 需要手动删除：run `44482953922` / artifact `14509376634`；run `44485479649` / artifact `14510716115`。删除前不得合并；新工作流不会自动删除旧 artifact。
- 本次修正不改变来源用途判断，不代表 NASA/JPL-Caltech 已批准本项目使用；不运行 TripoSR，不生成星敏感器模型。
