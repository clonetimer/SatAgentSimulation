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

## CHG-DT0-009：SRP 与地球辐射优先复用 Basilisk 2.11.0 官方面片/环境模块

**日期：** 2026-11-20  
**触发证据：** 官方 AVSLab/basilisk v2.11.0 标签源码和 Release Notes。

**调整内容：**
- 保留 Cannonball SRP 作为低成本对照模型。
- R1 先把真实 SPICE Sun 状态和 Eclipse illumination 接入当前 SRP。
- 根据几何成熟度选择官方 facetSRPDynamicEffector（内部面片/铰接面）或 facetedSRPEffector（外部面片几何与投影面积）。
- 将 EarthRadiationModel 的 albedo/IR 通量作为独立环境模型评估；不把通量输出当成已耦合的整星受力。
- TID/SEE/辐射带仍作为独立高能粒子环境任务。

**必要性：**
Basilisk 2.11.0 已提供官方 SRP 面片模型和地球反照/红外环境模型。若重新实现面片光压和环境公式，会重复成熟代码；但直接接入 facetedSRPEffector 又要求可信的面片几何和投影面积，当前 CAD as-built 映射尚未批准。

**充分性：**
先验证真实 Sun/Eclipse/Cannonball 路径，再按几何来源选择官方面片模块；对面片面积、法向、光学参数、铰接、投影面积和力矩分别建立测试。EarthRadiationModel 先验证通量，若没有真实力/力矩连接则不声称动力学闭环。该分阶段路径既复用官方实现，又不把参考 CAD 冒充实装。

**影响：**
不改变 R0 当前模型；将 R1 的 SRP 从“仅 Cannonball”扩展为“真实星历/地影 + 官方面片模型候选”，并将 Earth IR/albedo 与高能粒子辐射区分。

**验收条件：**
- 真实 Sun/Eclipse 消息驱动 SRP；
- 面片几何和投影面积来源可追溯；
- 力/力矩 A/B 与理论值及 Basilisk 单元测试一致；
- EarthRadiationModel 输出与参考输入一致；
- 未接入的高能粒子辐射效应继续显示为 unsupported。

## CHG-DT0-010：NRLMSISE-00 接入必须采用离线空间天气快照、全时段覆盖检查和真实整星阻力闭环

**日期：** 2026-11-20  
**触发证据：** Basilisk v2.11.0 MsisAtmosphere、SpaceWeatherData、DragDynamicEffector 和 ZeroWindModel 官方接口审计。

**调整内容：**
- 在推荐 whole_spacecraft.unified_native.v1 路径中增加显式 opt-in 的 NRLMSISE-00 + drag 模型，不改变 R0 默认运行。
- 复用 Basilisk 官方 SpaceWeatherData 发布的 23 条消息、MsisAtmosphere 密度/温度模型、ZeroWindModel 地球共转速度和 DragDynamicEffector。
- 新增离线 CelesTrak 资产获取器、SHA-256 manifest 和仿真全时段日期覆盖检查。
- 记录实际密度、空间天气输入、阻力矢量/模长和阻力加速度；Run Bundle 保留空间天气资产指纹。
- 当前第一版使用固定投影面积和 Cd，显式标记为工程假设；CAD 面片气动、姿态相关面积、真实热层风和高阶稀薄气体模型留待后续阶段。

**必要性：**
官方 SpaceWeatherData 在日期覆盖不足时会保留上次输出值并发出 warning；如果只调用官方模块而不做全时段覆盖检查，运行可能继续使用 stale Ap/F10.7。只记录 density 而不把 DragDynamicEffector 加入同一整星动力学图，也不能证明阻力真实改变了轨道。

**充分性：**
在运行前检查 CSV schema、严格递增/唯一日期、数值有效性、SHA-256 和 [epoch-3 days, epoch+duration] 连续覆盖；运行时将 23 消息、MSIS、ZeroWind 和 DragDynamicEffector 接入同一 Basilisk Process/Task；对密度、力和轨道响应进行独立 A/B。该路径可证明当前定义范围内的原生 NRLMSISE/阻力链，但不能证明真实气动外形或飞行精度。

**影响：**
默认 R0 不变；只有显式选择 atmosphere_model=nrlmsise00 才启用。缺少数据文件、UTC 日期覆盖不足或环境模型不匹配时拒绝运行。

**验收条件：**
- 官方 Basilisk 2.11.0 原生模块在同一 SimBase 中实例化并连接；
- CelesTrak 文件完整覆盖整个运行窗口和三日回看窗口；
- 所有 23 条空间天气输入均有记录且来源可追溯；
- MSIS 密度为有限正值，DragDynamicEffector 实际力为有限且非零；
- 有/无阻力 A/B 显示可重复的轨道能量/半长轴变化；
- 步长细化和外部参考对比按任务容差完成；
- 报告继续注明固定面积/Cd 工程假设，不宣称 as-built 气动精度。

## CHG-DT0-011：以 Basilisk 的真实本体系力输出作为阻力验收依据

**日期：** 2026-11-20  
**触发证据：** PR #206 初次 native CI 运行中，NRLMSISE 密度输出为正，但以继承字段 forceExternal_N 计算的阻力模长为零；逐行对照 Basilisk v2.11.0 的 dragDynamicEffector.cpp，发现 cannonballDrag() 实际更新 forceExternal_B，而不更新继承的 forceExternal_N。

**原方案与调整：**
- 原方案从 drag effector 的 forceExternal_N 求模，并声明 inertial-frame force。该声明缺少模块自身赋值证据。
- 改为从官方模块实际更新的 forceExternal_B 记录本体系阻力，以欧几里得范数求力的标量模长；原无依据的 inertial force XYZ 字段删除。
- 同步记录大气相对速度 v_B、方向 v_hat_B，以及 F_B·v_B（理论上应非正）。
- 将验收从“密度为正”提升为“本体系力非零、方向耗能、同初值有阻力/无阻力 600 s A/B 对比最终半长轴”，原有数值收敛断言不放宽。

**必要性：** 从未更新的继承字段取值会制造“零阻力”假象，误导数据使用者；仅有模型模块实例和正密度不能证明动力学确实受阻力作用。

**充分性：** 直接使用被官方 C++ 动力学计算赋值的力变量，结合非零相对速度、F_B·v_B<0 和同条件轨道能量差，可跨过字段合同、作用方向及动力学响应三个独立层面的验证。后续还必须保留数值步长收敛、任务容差和外部参考 V&V，不能把本轮工程 A/B 当成飞行精度证明。

**影响：** 改变新 DT1-A1 候选模型的输出字段合同和验收测试；不影响 R0 默认配置，不改变 Basilisk C++ 模型或旧 Run Bundle。修复初次记录时尚未通过同头 native CI；后续真实验收结果见下方核销证据。

**验收条件：** 当前 PR 同一 head 的官方 Basilisk 2.11.0 native CI 跑完，力、相对速度、耗散功率、半长轴 A/B 与旧回归全部 PASS；失败时先查真实物理及调用相位，不放宽断言。

**核销证据（2026-11-20 原生测试；2026-11-21 集成复核）：** PR #206 原始物理验收 HEAD `ffecb139f1f9861a80e1ba6ff7d091eff66f7a30` 的 [native CI #44755915226](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44755915226) SUCCESS，环境 12/12 PASS；61 样本最大 `forceExternal_B` 阻力 `3.725094387e-02 N`、最小耗散功率 `-2.740595737e+02 W`，同初态 600 s 半长轴差 `-3812.304821376 m`。测试专用质量 10 kg、面积 10 m²、Cd=2.2 与合成天气夹具均非默认参数或飞行标定。PR #213 集成候选 HEAD `c385d9067109afe9d0b49d90e6e5cabff2030495` 的 [原生整星 CI #44810938798](https://github.com/clonetimer/SatAgentSimulation/actions/runs/44810938798) SUCCESS，四环境效应同任务与正式 TaskSpec/脚本/Run Bundle 产品路径通过；集成 HEAD 15 SUCCESS、1 条件性 SKIPPED、0 FAIL。此核销只证明候选分支工程受力与内部动力学，不等于 main 已合并、独立轨道真值相关或飞行级验证；后继文档提交需独立检查最终 HEAD。


## CHG-DT0-012：空间天气资产的“哈希一致”与“真实来源”分离记录

**日期：** 2026-11-20  
**触发证据：** 对离线获取脚本及测试夹具的复审发现：未实际下载的既有 CSV，如果默认使用 CelesTrak URL 作为 source_url，会把本地测试/未知来源数据错误标注为官方下载物；单纯 SHA-256 只证明当前文件身份，不证明数据来源。

**原方案与调整：**
- 实际通过下载脚本取得的快照才写入下载 URL 与真实检索时间。
- 对已有离线 CSV，除非用户明确提供 --source-url 或已有经哈希匹配的 manifest，否则 source_url 留空、检索时间标记未知。
- 运行时若有相邻 manifest，先核对 CSV 哈希；哈希不符即失败关闭。没有 manifest 时明示 NOT_PROVIDED，不凭空补来源。
- 真实 Basilisk 集成测试使用合成的 CelesTrak 兼容数据，明确登记 test-fixture:// 来源，不冒充官方空间天气观测数据。

**必要性：** 误标来源会破坏 DT-3 的模型/数据证据链，也会错误提高置信度；直接用哈希字段或 CI 测试定义不能证明发布者身份。

**充分性：** “是否亲自下载/是否显式声明/是否未知”三种来源路径都有确定元数据和测试；基于 CSV 哈希的 manifest 绑定可以验证本地一致性，缺失的来源信息继续可见。该措施不宣称实现外部数字签名验证，也不从摘要推断真实飞行数据。

**影响：** 仅改变新 DT1-A1 资产获取/证据语义及对应测试；不改变原始 CSV 或既有 R0 仿真数据。若原来源不明，使用者必须看到 UNKNOWN，不得被隐式升级。

**验收条件：** 离线夹具、显式来源、未知来源、manifest 哈希篡改四类测试 PASS；Run Bundle 保留来源状态/哈希/覆盖范围；官方来源的真实性须另由可信发布渠道验证。
