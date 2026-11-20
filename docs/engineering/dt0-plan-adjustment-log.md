# DT-0 计划调整记录

本文件记录高可信仿真与数字场景升级计划 V0.1 的每次范围/顺序调整。调整不能覆盖原计划历史；每条必须同时说明必要性与充分性。

## CHG-DT0-001：复用现有 spice_wmm 环境，而不是从零建设轨道环境引擎

**日期：** 2026-11-20  
**触发证据：** 主干源码审计  
**原计划表述：** DT-1 建设高保真轨道与空间环境，容易被理解为从零实现 DE430/J2/WMM/日月第三体。

**调整内容：**
- 将现有 whole_spacecraft.unified_native.v1 的 spice_wmm 作为推荐整星环境的 R0 基线。
- 该路径已有离线 DE430、地球 J2、Sun/Moon 第三体和 WMM2025。
- DT-1 的重点改为审计资源、时间、坐标和消息链，随后补齐 J3/J4、NRLMSISE-00、阻力和完整 SRP。
- 固体潮/海潮进入 LEO-Precision 任务配置，不默认施加到全部 LEO 工况。

**必要性：**
现有实现已经存在且进入推荐整星路径；从零再建会重复实现，并增加两套环境状态/时间源不一致的风险。

**充分性：**
通过 spice_wmm 实际运行、资产哈希、同历元日月/地磁/日食消息核对和独立轨道验证，可以证明复用路径可靠；缺失模型另设明确验收，不以现有 J2/WMM 能力替代 J3/J4/NRLMSISE/SRP。

**影响：**
不改变现有模型和 TaskSpec；调整 DT-1 实施顺序。R0 仍不宣称高保真。

**验收条件：**
- 推荐整星真实 Run 记录环境 profile、DE430/WMM/J2 资产来源及 SHA-256。
- 轨道、地磁、日食消息时间和参考系一致。
- 缺少资产时 fail closed。
- 新模型逐项通过独立测试后才晋升为正式能力。

## CHG-DT0-002：从“器件非理想缺失”调整为“逐器件、逐后端、逐运行路径补齐”

**日期：** 2026-11-20  
**触发证据：** unified_native、ADCS、sensor、reaction wheel 和组件能力审计  
**原计划表述：** DT-2 容易被理解为重新实现所有传感器和执行机构。

**调整内容：**
- 复用现有 RW 饱和/摩擦/实际力矩、IMU 偏置/噪声、星敏/磁强计安装参数及整星故障事件。
- 不把单机 fixture、项目代理、官方 Basilisk 原生和整星闭环混为一谈。
- 对 Basilisk 2.11 磁强计噪声已知限制，先做官方版本/实现验证，不用 Python 后处理伪造原生噪声。
- 新增内容优先聚焦死区、时滞、温漂、量化、杆臂和随机模型复现等缺口。

**必要性：**
已有物理模型和验收资产足以复用；重复造模型会造成模型来源分裂、参数含义不一致和重复验证成本。

**充分性：**
每个器件建立 source→model→message→recorder→Run Bundle→scene 的追踪矩阵；只有在同一整星图或明确独立设备范围内真实运行并通过物理 A/B，才算相应能力完成。

**影响：**
DT-2 从“全量新建”改为“能力盘点、缺口修复、统一接入和 V&V”；不降低噪声、偏置、安装误差等目标要求。

**验收条件：**
- 器件参数真实消费；
- 非理想输入与输出变化有可复核证据；
- 同 seed 复现和统计分布测试通过；
- 整星联合路径有直接消息链证据；
- 不支持的物理效应明确显示 unavailable。

## CHG-DT0-003：前端大数据要求拆成“现有预览抽样”和“目标级渲染能力”

**日期：** 2026-11-20  
**触发证据：** telemetry_overview.py、satellite-scene.js、birds-cad-viewer.js 和 P9-I 最新性能制品  
**原计划表述：** 需要建设万点曲线和百万级轨迹渲染，容易忽略现有抽样和边界。

**调整内容：**
- 保留 Run 预览当前最多 3000 个实际样本点、全局极值保留和原始数据不插值的设计。
- 将场景轨迹 600 点上限明确登记为当前限制。
- 把百万级轨迹作为独立 GPU buffer/LOD 性能目标。
- BIRDS-4 CAD 固定为全尺寸性能回归模型；优先降低 Draw Calls 和对象数，不先盲目简化几何。

**必要性：**
当前已具备内存有界的曲线预览，不应重做；但 600 点轨迹和 26,427 Render Calls 的 CAD 模型不能满足目标级数据量与性能要求。

**充分性：**
使用固定输入规模、固定视口、指定 GPU/浏览器和可重复采样，分别记录加载时间、帧时间 P50/P95/P99、Render Calls、GPU/CPU 内存及交互响应。抽样策略必须保留极值和事件，原始数据不被覆盖。

**影响：**
DT-4/DT-5 增加独立性能基准；不改变 Run Bundle 原始数据和当前预览合同。

**验收条件：**
- 预览与原始数据明确区分；
- 曲线和轨迹均有规模阶梯测试；
- 真实 GPU 测试与 CI 软件 WebGL 测试分开报告；
- 优化前后有相同模型和视口的性能证据。

## CHG-DT0-004：首期采用 LEO-Engineering-R0/R1 双基线，不把现状误标为目标完成

**日期：** 2026-11-20  
**触发证据：** 当前推荐整星环境限制、Capability 状态和外部参考数据边界  
**原计划表述：** 首期 LEO 高保真闭环可能把现有可运行模型与未来目标混为一谈。

**调整内容：**
- R0 记录当前真实可执行的产品路径和限制。
- R1 定义首期 LEO-Engineering 必须补齐的 J2–J4、NRLMSISE-00、SRP、器件非理想和 V&V。
- 固体潮/海潮按 LEO-Precision 任务容差启用。
- 外部参考仿真统计仅作为模型间一致性参考，不升级为真实标定/飞行验证。

**必要性：**
现有推荐整星路径真实运行但不包含全部目标力模型；如果没有 R0/R1 分离，容易出现用旧模型结果宣称新要求完成的问题。

**充分性：**
R1 每项要求都有独立模型、输入数据、数值基准、任务容差和 Run Bundle 证据；R0 可持续用于回归比较。

**影响：**
首期目标更清晰；未减少用户提出的高保真要求，仅将潮汐等精度相关模型分配到明确任务档位。

**验收条件：**
- R0 结果可复现；
- R1 差异项逐项关闭；
- 任务级容差在测试前冻结；
- 不以内部统计相似、CI 通过或模型名称代替独立物理验证。

## 调整审批规则

后续每条变更必须新增编号，不得改写或删除既有记录。若调整影响物理模型范围、数据来源、任务容差、接口合同或证据标准，必须先给出变更理由和回归影响，再实施。

## CHG-DT0-005：外部参考统计只用于仿真间对齐，不作为硬件/飞行标定

**日期：** 2026-11-20  
**触发证据：** configs/reference_alignment/external_reference_statistics.json

**调整内容：**
- 将该数据明确限定为参考仿真输出统计之间的一致性对齐。
- 不将其纳入 ground_calibrated 或 flight_correlated 参数证据。
- 不根据摘要推断未提供的原始时序、列映射、单位/坐标约定或容差。
- 更细粒度比较必须向数据持有人提出具体文件、列和统计定义需求。

**必要性：**
当前登记文件明确标注原始 CSV 未提供、未读取、未重建，参考数值容差为空，部分列映射和统计定义尚未解决。把它作为真实标定数据会超出证据能力。

**充分性：**
保留现有来源声明、原始数据缺失状态和待补字段；只有收到可核验原始数据、数据字典、校准方法及独立验证残差后，才允许改变参数证据等级。

**影响：**
不改变当前模型参数，不修改评测容差，不改变已有运行结果；仅冻结数据用途和报告口径。

**验收条件：**
- 所有对齐报告继续标注 simulation-output reference；
- 不输出 hardware-calibrated 或 flight-correlated 结论；
- 缺失统计不进行插值、猜测或反向重建。

## CHG-DT0-006：将真实 spice_wmm 运行测试纳入官方 native CI

**日期：** 2026-11-20  
**触发证据：** tests/test_native_orbital_environment.py 与 .github/workflows/continuous-satellite-native.yml 的对照审计

**原计划状态：** DT-1 后续再补强环境 V&V。

**调整内容：**
- 将现有 spice_wmm 整星运行测试提前作为 DT-1 的第一个 CI 门禁。
- 在官方 native workflow 中增加 tests/test_native_orbital_environment.py 的真实运行步骤，并将该测试、orbital_environment.py 纳入路径触发。
- 继续使用官方 bsk==2.11.0 和现有离线 DE430/WMM/J2 资源，不创建新的环境后端。

**必要性：**
当前 workflow 会下载并 preflight DE430/WMM/J2 资源，但没有执行已有的 native orbital environment 测试文件。资源存在不等于 Basilisk 真实运行成功；没有 CI 门禁，环境路径回归可能不被发现。

**充分性：**
该测试文件包含 UnifiedNativeRuntime 的真实 spice_wmm 运行、不同 UTC 历元下的 Sun/WMM 输出核验，以及 3600 s 轨道步长细化测试。将其加入官方 Basilisk 2.11.0 工作流，可以把资源 preflight 提升为可重复的真实运行门禁。它仍不能替代第三体加速度定量 A/B 或 STK/GMAT 外部交叉验证。

**影响：**
只增加已有测试的 CI 执行，不改变模型、参数、积分步长、任务容差或生产运行行为。

**验收条件：**
- 官方 native workflow 同一 head 实际执行该测试文件；
- 所有测试无 skip/failure；
- 保存运行日志和环境资产指纹；
- 如测试失败，先修复真实运行/测试问题，不放宽物理断言。


**实施进度记录（2026-11-20）：**
- 实施 PR：[ #204 ](https://github.com/clonetimer/SatAgentSimulation/pull/204)，head SHA 为 d3c080eac16861694d9e673eb636830e31bbaa64。
- Actions run：[44734515475](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44734515475)。
- 新增的 “Verify actual native SPICE WMM environment and orbit-step convergence” 步骤已完成 SUCCESS。
- 同一 native workflow 的其他连续仿真、器件物理和长时回归仍在运行；本记录只确认新增步骤通过，不将整条 workflow 提前标为 PASS。


## CHG-DT0-007：保留 WMM2025 并增加显式 IGRF 模型选项，不静默替换

**日期：** 2026-11-20  
**触发证据：** 推荐整星 spice_wmm 已使用 Basilisk MagneticFieldWMM/WMM2025；用户提出的目标环境模型明确列出 IGRF。

**调整内容：**
- 保留现有 WMM2025 路径作为 R0 兼容基线。
- DT-1 将 IGRF 作为独立、显式版本化的磁场模型选项评估/接入。
- TaskSpec、环境快照、Run Bundle 和场景数据来源必须记录实际使用的磁场模型、版本、历元和系数资产哈希。
- 不把 WMM2025 的运行证据改名为 IGRF 证据，也不在不记录模型版本的情况下替换默认模型。

**必要性：**
WMM 和 IGRF 都是地磁参考模型，但模型系数、版本周期、有效时间范围和误差特性不同。直接将当前 WMM 路径改称 IGRF 会造成物理来源误标，并破坏 R0 结果可比性。

**充分性：**
通过保留 WMM2025 作为显式兼容选项，并对 IGRF 独立实现、独立测试和记录模型元数据，可同时满足当前整星回归和目标模型要求；只有在任务精度与验证结果支持时才调整默认选择。

**影响：**
不改变当前 spice_wmm 输出，不立即切换默认磁场模型；为 DT-1 增加一个版本化环境模型选择任务。

**验收条件：**
- WMM2025 与 IGRF 使用独立 model_id/version；
- 同一时刻、同一坐标系可输出可比较的地磁矢量；
- 模型适用时间范围和数据资产哈希被封存；
- 磁强计真实 Basilisk 输入/输出 A/B 证明环境模型切换确实影响测量；
- 报告不得将 WMM 结果标注为 IGRF。

## CHG-DT0-008：NRLMSISE-00 优先复用 Basilisk 2.11.0 官方模块，不引入重复 Python 实现

**日期：** 2026-11-20  
**触发证据：** 官方 AVSLab/basilisk v2.11.0 标签源码及 2.11.0 Release Notes 核查。

**调整内容：**
- 将 DT-1 的 NRLMSISE-00 工作从“选择/引入第三方 Python NRLMSISE 包”调整为“集成官方 Basilisk MsisAtmosphere + spaceWeatherData + DragDynamicEffector”。
- 以固定版本的 CelesTrak 格式空间天气 CSV 作为输入资产，保存来源、检索时间、SHA-256 和有效日期范围。
- 在 SatAgentSimulation 侧补充日期覆盖、UTC epoch、23 消息顺序和 stale-data 质量检查。
- 保留指数大气作为明确的低保真对照模型，不将其替代为 NRLMSISE。

**必要性：**
官方 Basilisk v2.11.0 已包含 NRLMSISE-00 的 C++ 模块和空间天气数据模块。重复引入 Python 实现会形成两套公式、单位、数据时标和消息质量路径，增加结果不一致和性能不确定性。

**充分性：**
MsisAtmosphere 已提供 NRLMSISE-00 密度/温度输出；spaceWeatherData 能从 UTC EpochMsg 和 CelesTrak CSV 生成所需的 23 条 SwDataMsg；官方 scenarioDragDeorbit 展示了与 DragDynamicEffector 的连接模式。通过项目级数据快照、完整日期覆盖检查、真实 Basilisk A/B 和轨道收敛测试，可完成产品接入而不重写模型。

**重要限制：**
spaceWeatherData 不做空间天气插值；日期缺失时会保留上次发布值并警告。因此项目必须在运行前检查完整数据覆盖，并在运行中监控有效性，不能静默沿用 stale 值。ZeroWindModel 只表示地球共转，不代表真实热层风。

**影响：**
不改变当前 R0 的指数大气/无阻力行为；DT-1 的实现顺序改为先接入官方原生模型和数据资产，再做高保真受力验证。

**验收条件：**
- 在官方 bsk==2.11.0 下真实加载 MsisAtmosphere、spaceWeatherData 和 DragDynamicEffector；
- 对 CelesTrak CSV 做 schema、日期唯一性、覆盖范围、SHA-256 检查；
- 对比同一轨道下指数大气与 NRLMSISE 密度、阻力和轨道响应；
- 验证 Ap/F10.7 变化会影响模型输出；
- 验证数据缺失、日期越界、UTC epoch 错误时 fail closed 或显式降级；
- 完成整星同一 Basilisk Process/Task 的消息连接和 Run Bundle 溯源。
