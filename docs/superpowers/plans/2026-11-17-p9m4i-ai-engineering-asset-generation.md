# P9-M4-I：AI-assisted Engineering Asset Generation

日期：2026-11-17  
基线：P9-M4 统一映射契约已进入主线；PR #159/#160 仍是相邻的映射与 GLB 节点交叉核验工作，不依赖本实验分支合并。

## 目标

验证生成式 3D 与参数化 CAD 能否经济地补全缺少原始 CAD 映射的设备外观，并将结果安全接入 SatAgentSimulation。第一轮只做三个代表对象：电池组、反作用轮、星敏感器。

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

| 对象 | 参数化路线 | 图像生成路线 | 主要验证 |
|---|---|---|---|
| 电池组 | 长方体壳体、盖板、接口和安装孔 | 单机照片生成外观网格 | 包络尺寸、孔位、连接器 |
| 反作用轮 | 圆柱/轮体、轴线、支架与安装面 | 多视角产品图生成外观 | 轴线、包络、安装接口、旋转语义 |
| 星敏感器 | 外壳、遮光罩、光学窗口和安装基准 | 产品图生成外观 | 光轴方向、窗口位置、包络 |

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

1. 资料准备：每个对象至少收集一份来源可追溯的正视/侧视/俯视或产品图；有尺寸图则优先。
2. 参数化基线：构建简化但尺寸可控的 FreeCAD/OpenCascade 模型。
3. 生成模型：在隔离 Linux GPU worker 上运行 TRELLIS.2；对同一输入记录模型 commit、checkpoint、配置、耗时和资源峰值。
4. 输出统一为 GLB；保留原始 STEP/FCStd（若有）、源图、生成日志和哈希。
5. 运行本仓库的无第三方依赖 GLB 审计器。
6. 比较参数化与生成式输出：包围盒偏差、网格完整性、尺度一致性、外观可辨识性、人工修复时间、生成耗时和成本。

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

第一轮技术试验规划 5–8 人日；该数字为工程计划估算，不是模型服务商报价。只有在三个设备都能完成端到端生成/审计/加载，且人工修复时间低于手工从零建模基线时，才扩大到 16 类批量补全。

## 本阶段交付

- 合成几何资产 schema 与注册验证器；
- 无第三方依赖 GLB 质量审计 CLI；
- 三设备 benchmark manifest；
- 许可、硬件、模型隔离和质量门禁文档；
- 单元测试与 CI；
- 不修改 Basilisk 仿真模型，不新增核心运行时 AI 依赖。


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

The GLB auditor now also checks primitive mode, material references, index accessor scalar/component types, triangle index divisibility and non-indexed triangle vertex divisibility. These checks are structural validation only, not proof of geometric accuracy.

A three-device source-intake worksheet has been added. It specifies the minimum original photos, dimensions, interface drawings and hardware identity needed for battery, reaction wheel and star tracker. Current source status is explicitly `blocked_source_intake`; no device-specific dimensions or models have been fabricated.
