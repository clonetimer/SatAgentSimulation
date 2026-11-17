# P9-M4：工程数字卫星统一映射契约

日期：2026-11-17  
基线：P9-M3 核心审查与图件提取已合并；M3 仍有三项候选的人工实装证据审批未完成。

## 目标

统一整星工程对象、CAD 几何资产和 Basilisk 物理状态之间的映射表达，同时严格保留三类数据的身份边界。M4 首批交付是可验证的映射契约和 fail-closed 验收，不把 M3 尚未审定的 BIRDS-4 候选提升为真实装机实例。

## 四种互斥映射类型

1. `as_built_verified`：具体飞行单元的已审定实装实例。必须具备实例 ID、父实例锚点、批准 frame/datum、安装变换、硬件件号/版本和五类批准证据。
2. `simulation_configuration_geometry`：当前 TaskSpec/Run 中的仿真构型几何，例如 Basilisk RW 的 `rWB_B`、`gsHat_B` 或 IMU 的安装参数。必须绑定 Run/TaskSpec；不能声明 as-built instance 或飞行单元实装位置。
3. `reference_cad`：来源可信但适用飞行单元、datum 或安装关系未审定的参考 CAD。仅允许在参考 CAD 视图中查看；不允许创建实例、空间锚点或遥测绑定。
4. `unlocated`：工程对象类别存在，但没有可信空间资产映射。保留对象树和参数入口；不生成 mesh、位置或朝向。

四种类型不可互相隐式转换。特别是 CAD 节点的 source world transform 不得复制成 as-built installation transform；仿真参数几何也不能被解释为型号实装坐标。

## as-built 提升的五项强制证据

- `flight_unit_identity`：目标型号和具体飞行单元；
- `hardware_identity`：批准件号、硬件版本和修订；
- `assembly_relation`：父子装配关系及批准图纸/程序；
- `coordinate_datum`：原点、轴向、单位和手性；
- `installation_transform`：批准的父子坐标变换链。

每项必须包含来源哈希、精确文档/图纸定位、审查人和 approved 决定。缺一项即 fail closed。

## 当前 M3 候选状态

Battery、Reaction Wheel、Perovskite Solar Cells 三个候选均继续登记为 `reference_cad`。目前的 CAD 矩阵数学检查、Structural Testing Model 程序和三颗卫星的振动/TVT 测试报告均不能单独满足五项 as-built 证据。当前 as-built verified 实例数量为 0。

## 交付

- `src/sat_sim/engineering_asset_mapping.py`：统一映射验证器。
- `tests/cad/test_engineering_asset_mapping.py`：四种映射类型、证据缺失、变换手性、重复实例 ID 和错误声明测试。
- `.github/workflows/p9m4-unified-mapping.yml`：独立 M4 契约门禁。
- 本文档：数据身份、状态转换和提升要求。

## 首批验收

1. 四种映射类型分别通过各自契约。
2. as-built verified 必须具备五类已批准证据、有效刚体变换和父实例。
3. reference CAD 不可绑定遥测或创建实装实例。
4. simulation configuration geometry 必须 Run/TaskSpec 作用域化，且不声明 as-built。
5. unlocated 对象不允许伪造几何或空间锚点。
6. 同一 asset_instance_id 不可重复。
7. 真实 M3 候选输入后，as-built verified 数仍必须为 0，直到人工证据审批完成。

## 后续集成

契约通过后，再将其接入数字场景的资产注册表和右侧对象检查器：
- 参考 CAD、仿真构型、实装装配三个视图明确分开；
- 对象树选择、资产节点、物理参量与 Run 时间同步；
- 未定位对象显示“未建立空间映射”；
- 只有 `as_built_verified` 可以进入真实装配视图；
- 历史 Run 与实时遥测仅绑定同一已验证实例，或显式的仿真构型几何，不跨越身份边界。

M4 的契约测试通过不代表整个工程数字卫星已经验收；还需真实资产注册、UI 交互、桌面/窄屏浏览器和长序列回归。
