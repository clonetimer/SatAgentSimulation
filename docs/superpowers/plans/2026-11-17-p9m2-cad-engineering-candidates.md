# P9-M2：CAD 节点与工程对象候选映射

日期：2026-11-17  
基线：P9-L4 已合入 main（merge commit `f537d0c905a93b9220028b5695bc4f0744bd0694`）

## 目标

建立 CAD 装配树节点与 `workbench-object-tree.v2` 设备类别之间的可追溯候选关系，为 P9-M3 实装坐标/装配证据审查提供结构化输入。M2 只产生候选，不创建装机实例，不生成空间锚点，不允许候选直接进入真实装配渲染。

## 数据输入

- 工程侧：通过 `scripts/cad/export_workbench_engineering_tree.py` 从正式 workbench presentation catalog、scene catalog 和 capability schemas 实时生成 `workbench-object-tree.v2`。仅将 `kind=equipment` 的设备类别作为候选目标；外部设施、任务上下文、链路关系和分组节点不参与硬件匹配。
- CAD 侧：`convert_birds4_step.py` 生成的 assembly-tree JSON，保留 `source_node_path`、原始标签、父节点、重复 occurrence、局部/世界变换和几何/子节点计数。
- 来源身份：CAD 模型、上游固定 commit、STEP 文件路径及后续转换报告哈希。

## 候选匹配策略

第一批只采用保守、可解释的名称别名规则，并且只在具有子节点的 CAD 装配 occurrence 上产生部件候选。无子节点的 shape/geometry leaf 保留在原始装配树中，但不被解释成独立设备；避免将一个传感器的多个几何实体误计成多台设备。机械固定件、导向件、垫圈、支架和连接器等功能性附件也不能仅因标签含有 antenna 等词就映射为功能设备。每条候选必须保存：
- 稳定 candidate ID（工程 object ID + CAD 节点路径哈希）；
- 工程对象 ID、CAD 源节点路径、原始节点名和标签；
- 命中的别名规则及 lexical match strength；
- FM/EM/B3 等来源变体标签；
- CAD 节点粒度（assembly occurrence）、直接几何数和子节点数；
- 多 CAD 节点对应一个工程类别、一个 CAD 节点对应多个工程类别的歧义标记；
- 待完成的几何、坐标、安装证据和身份审查状态。

Lexical match strength 是规则强度，不是概率，不代表真实性。泛化的 panel、board、sensor、module、COM、OBC 标签不能单独证明部件功能；-X 面板不得仅凭 panel 名称归为太阳电池阵。

## 身份与安全边界

- 设备类别不是实际装机清单；不从类别数量推导实例数量。
- 保留重复 occurrence 和不同父路径，不把重复标签合并成单台设备。
- EM/B3 命名节点可以进入候选审查，但必须标记 flight-unit identity review；不能自动当作 BIRDS-4 FM 实装。
- 每条候选的 `instance_id=null`、`spatial_anchor=null`、`render_eligible=false`。
- 无候选不等于 CAD 中不存在该设备；表示当前保守词典没有足够名称证据。
- M2 不读取或修改 Basilisk 物理状态，不更改 TaskSpec、仿真模型和实时遥测契约。

## 交付

- `src/sat_sim/cad_candidate_mapping.py`：纯函数候选生成器及 JSON CLI。
- `scripts/cad/build_engineering_candidates.py`：仓库内 CLI 入口。
- `tests/cad/test_candidate_mapping.py`：别名、变体、重复 occurrence、外部对象排除、歧义、确定性及 fail-closed 测试。
- `scripts/cad/export_workbench_engineering_tree.py`：导出正式工程对象树，避免手工维护第二份设备类别清单。
- `.github/workflows/p9m2-cad-candidates.yml`：候选映射、CAD 来源和现有对象树契约的联合门禁；从 P9-I 真实浏览器验收所用的固定 BIRDS-4 CAD artifact 读取 855 节点 assembly tree，生成可下载的真实候选审计报告。

## P9-M2 验收

1. 候选可由相同输入确定性重建。
2. 每个候选都能回溯到唯一 CAD 节点路径和工程 object ID。
3. 多实例、多候选、重复标签与 EM/B3 身份风险均显式标记。
4. 泛化名称不产生未经支持的部件映射；几何 leaf 和安装辅件不被当作设备实例。
5. 不创建 verified 映射、装机实例、安装坐标或可渲染节点。
6. 新测试、CAD 来源测试、对象树测试全部通过。

## 后续

- P9-M3：结合 FM Assembly Procedure、型号/版本图纸和 datum 证据，逐项审查候选身份、父子装配关系、坐标系和刚体变换。
- P9-M4：只有经过审定的实例才进入统一映射契约；未审定候选继续保持 draft，不影响仿真。


## P9-M2 classification correction — Perovskite Solar Cells

The first full-FM candidate audit exposed a semantic collision: the CAD node \`Perovskite_Solar_Cells v3\` was lexically matched to the bus \`component.solar_panel\` category. This is not supported by the mission documentation.

The BIRDS-4 on-orbit paper separately lists the Perovskite Solar Cell (PSC) Mission and describes the bus power source as Azur 3C30 solar cells; it also describes five bus solar panels on the external spacecraft faces. Therefore the perovskite cell assembly is treated as a **payload candidate**, not the spacecraft bus solar array:
- Paper: [BIRDS-4 1U CubeSat Missions and On-Orbit Results](https://nanosat11th.itu.edu.tr/assets/papers/BIRDS-4.pdf)
- The assembly remains a candidate only; the paper does not establish a specific flight-unit as-built placement transform.
- The mapper now maps the explicit Perovskite Solar Cells label to \`component.payload\` and excludes it from \`component.solar_panel\`.

This correction demonstrates why lexical aliases must be reviewed against mission function, not just shared words such as “solar cell”.


## P9-M2 source-level variant identity gate

The full \`BIRDS4_FM_Final v7.step\` source tree contains both EM- and B3-labelled nodes in addition to FM-labelled nodes. Even when an individual candidate path itself carries an FM label, the parent full-reference assembly is not a clean flight-unit-specific inventory. Therefore every candidate derived from this mixed full assembly now inherits \`identity_review_required=true\`. This is a source-level identity gate, not a claim that the candidate part itself is EM or B3.

P9-M3 must resolve each candidate against the pinned FM Assembly Procedure, the relevant approved drawing/revision and the exact flight-unit variant before any instance can be promoted. The CAD label and full-assembly filename alone are insufficient.
