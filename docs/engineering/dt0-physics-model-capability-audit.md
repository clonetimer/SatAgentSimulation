# DT-0 物理模型能力审计

**审计日期：** 2026-11-20  
**主干基线：** be9c581f73f538dc040cfa4fe4d419e9464f8513  
**目的：** 为 DT-1/DT-2 建立真实物理模型来源、运行路径和验证边界，避免重复实现及能力误标。

## 1. 先区分三条整星/轨道路径

| 路径 | Capability / 代码 | 当前定位 | 可以作出的结论 |
|---|---|---|---|
| 推荐整星产品路径 | whole_spacecraft.unified_native.v1；src/sat_sim/bsk_engine/unified_native.py | 推荐的整星 Basilisk 混合原生运行图；官方 Basilisk 模块与项目 SysModel 在同一 Process/Task | 可证明当前已声明并实际执行的轨道、ADCS、EPS、热控、通信、载荷和可选推进链路；不能推断未接入的物理模型。 |
| 独立轨道原生扩展 | orbit_environment.basilisk_hf.v1；src/sat_sim/orbit/basilisk_hf.py | 显式 Basilisk-only 的实验/扩展适配器；BSK-ORB-2、BSK-RUN-3、BSK-RUN-4 | 可作为独立轨道模型扩展和烟测入口；不能自动等同于推荐整星主图已接入。 |
| 6-DOF 原生整星候选 | whole_spacecraft.basilisk_6dof.v1；src/sat_sim/spacecraft/basilisk_6dof.py | BSK-RUN-6，trust_level=experimental，status=bsk_run6_partial_runtime_coverage | 有最小惯性/姿态/反作用轮闭环路径；其他制导模式及 Drag/SRP/SPICE 组合存在配置与运行范围限制。不能与推荐整星产品路径混为一谈。 |

**重要边界：** 当前推荐整星路径的 capability 状态是 engineering_preview / agent_ready_candidate；它有实际运行证据，但这不等于全物理模型已经完成高保真验证。

## 2. 轨道与空间环境

### 2.1 推荐整星环境

入口：src/sat_sim/bsk_engine/orbital_environment.py

| 环境配置 | 实际模型 | 状态与边界 |
|---|---|---|
| simplified | 地球点质量、固定参考太阳、中心偶极磁场 | 历史简化参考环境；不能称为真实任务环境。 |
| spice_wmm | 离线 DE430、地球 J2、日月第三体、WMM2025 | 已接入推荐整星路径；WMM2025 有效年份限定 2025–2029；依赖本地固定版本资源，无隐式联网或代理回退。 |
| Eclipse | Basilisk Eclipse | 推荐整星环境中有真实消息连接；需要继续核验本影/半影边界和各下游模型同时间使用。 |

当前 spice_wmm 使用的地球重力文件为 GGM03S-J2-only；整星能力文件明确说明高于 J2 的地球谐项和大气阻力未建模。

### 2.2 独立 Basilisk 轨道扩展

入口：src/sat_sim/orbit/basilisk_hf.py、src/sat_sim/benchmarks/basilisk_orbit.py、src/sat_sim/adapters/orbit_environment_basilisk_hf.py

| 路线 | 已有实现 | 证据范围 | 不得扩大的结论 |
|---|---|---|---|
| BSK-ORB-1/2 | 中心引力、球谐接口、配置蓝图、内部轨道基准 | J2 路径及内部 benchmark 合同 | degree 字段允许 2–100，不代表当前系数文件包含对应阶次，也不代表高阶重力已通过验证。 |
| BSK-RUN-3 | SPICE、DE430 日月星历和第三体引力 | 真实内核可用时执行；缺内核必须报告 unavailable | 不等于外部轨道真值相关或飞行验证。 |
| BSK-RUN-4 drag | Basilisk ExponentialAtmosphere + DragDynamicEffector | 指数大气与阻力的独立 runtime smoke | 不是 NRLMSISE-00，也不是已并入推荐整星图的阻力模型。 |
| BSK-RUN-4 SRP | Basilisk RadiationPressure Cannonball | 独立 SRP runtime smoke | 当前烟测使用固定太阳位置和 illuminationFactor=1；不是完整日食耦合或表面光学模型。 |

### 2.3 轨道环境缺口

- J3/J4：需要完整重力场系数文件、系数归一化/参考半径/阶次核对、独立测试和任务级误差预算。
- NRLMSISE-00：当前没有整星正式接入；需定义 F10.7、Ap、UTC 和空间天气数据来源。
- 阻力：需要姿态相关投影面积、质量、气动系数和轨道/姿态一致的相对大气速度。
- SRP：需要真实太阳星历、日食遮挡、航天器表面法向/面积/反射率；当前 Cannonball 烟测不能替代该路径。
- 固体潮/海潮：当前未发现正式接入；按 LEO-Precision 任务需求决定。
- 地磁：推荐整星使用 WMM2025；用户需求中提到 IGRF，二者须作为显式模型选项或经过评审后选择，不能直接将 WMM 标成 IGRF。
- 辐射环境：当前未形成辐射带/粒子环境到器件效应的完整链路。

## 3. 姿态、传感器和执行机构

### 3.1 推荐整星统一图

入口：src/sat_sim/bsk_engine/unified_native.py、src/sat_sim/interactive/api.py、src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml

当前 whole.orbit 与 whole.adcs 分离发布。whole.orbit 包含轨道位置和刚体姿态真值；whole.adcs 包含实际发布的反作用轮状态/力矩、IMU、星敏、磁强计和太阳敏感器测量字段。浏览器只在同一 Session、Epoch、参考系、精确仿真时刻和 GOOD 质量下进行关联。

当前已通过的真实浏览器验收：
- P9-F：真实 Basilisk 遥测驱动 Globe.gl 场景。
- P9-L4：离线、限时实时、持续实时三条用户旅程。
- P9-K/L：轨道与 ADCS 独立质量域及严格时间关联。

### 3.2 器件边界

| 器件 | 已有真实模型/能力 | 关键未完成项 |
|---|---|---|
| 反作用轮 | 官方 ReactionWheelStateEffector；轮速、实际施加力矩、摩擦、力矩/速度限制；部分整星故障与退化事件 | 全工况摩擦/死区/时滞、转子热效应、完整安装误差及全轮拓扑闭环覆盖。 |
| IMU | 官方 ImuSensor；安装角和 sensorPos_B；部分整星偏置/噪声事件 | 独立随机噪声谱、随机游走、偏置温漂、量化、饱和、杆臂加速度及版本级 RNG 复现。 |
| 星敏感器 | 官方 StarTracker；四元数输出、安装方向、部分噪声/随机游走接口 | 星表、光学成像、星点质心、视场遮挡、盲区、失锁/重捕获；valid 不等于完成星识别。 |
| 磁强计 | 官方 Magnetometer；WMM 环境、安装矩阵、偏置和比例参数 | 推荐整星噪声能力受 Basilisk 2.11 已知实现限制；需核验噪声矩阵/RNG 版本问题，不能用参数字段代替实测噪声。 |
| 太阳敏感器 | 官方 CoarseSunSensor 有真实消息输入/输出和法线配置 | 当前主要是标量余弦强度；不等于三轴方向估计；视场、遮挡、失效和轨道历元环境需继续闭环验收。 |
| 磁力矩器 | 项目模型支持磁偶极矩限制和力矩计算；有相关构件接口 | 需确认推荐整星原生闭环路径、磁场来源、安装误差、线圈动态及实际力矩证据。 |
| 推力器 | 官方 ThrusterDynamicEffector/FuelTank 有独立有限时长 Run 和整星可选推进事件 | 当前整星推进有单个计划燃烧窗口等边界；阀门时延、最小冲量位、多脉冲和安装误差需补验。 |

### 3.3 单机验收与整星闭环的区别

P9-N4 已有九类设备经过不同程度的官方 Basilisk 独立有限时长 Run、Run Bundle 和 Chromium 历史回放验收。它们的模型边界并不相同：

- 部分传感器使用 prescribed truth fixture。
- IMU/星敏/磁强计的独立入口与整星闭环入口不是同一验收。
- 太阳翼单机使用固定 Sun/Eclipse 输入。
- 热节点是固定辐射板 fixture，不是完整热网络。
- 推力器独立 Run 不等于完整推进系统与轨道姿态闭环。

后续验收必须证明同一 SimBase/Process 中的真实消息链路和物理响应，不允许把多次单机 Run 后处理拼成整星闭环证据。

## 4. 确定性、时间与实时性

### 已有基础

- Canonical TaskSpec 1.0 支持 duration、step、sample、random_seed、epoch、time_system 和 solver。
- 规范化 TaskSpec、ResolvedSpec、ExecutionPlan 有 SHA-256。
- Run Bundle 保存 TaskSpec、参数来源、能力快照、运行环境、依赖版本和制品哈希。
- 求解器和多个模型采用固定步长；实时会话提供 pacing/drift 观测、命令队列和遥测质量状态。
- performance_budget.json 明确声明 soft_realtime，而非 hard_realtime。

### 尚未满足

- 没有目标星载计算机上的 WCET/最坏抖动证明。
- 没有 IEEE 1588 PTP 或 IRIG-B 生产接口及资格测试。
- random_seed 已进入 TaskSpec，但必须逐模型证明 RNGSeed 消费、重复运行复现和跨版本行为。
- Run Bundle 当前有运行环境和依赖版本记录，但需补齐 Git commit、外部 SPICE/WMM/重力系数资产哈希、随机流状态和有效模型版本的统一快照。
- 当前完整 native checkpoint/resume 不支持；历史 Run 回放不等于原生物理状态恢复。

## 5. 参数来源、校准和验证口径

Canonical TaskSpec 已有 demo、engineering_estimate、ground_calibrated、flight_correlated 参数档位，并保存 provenance。校准指南要求来源文件/数据集、SHA-256、单位、工况范围、不确定度、相关性和验证残差。

当前外部参考统计配置明确声明：
- 数据源是用户提供的仿真输出统计摘要；
- 未收到原始 CSV，未读取或重建原始时序；
- 未提供参考数值容差；
- 不能用于真实飞行/硬件标定或飞行级精度声明。

因此 DT-3 的 V&V 必须分开：
1. 代码/模块符合性；
2. 内部物理基准和收敛；
3. 独立工具交叉验证；
4. 地面硬件测试/标定；
5. 飞行数据相关性。

任一级证据都不能自动替代更高一级。

## 6. 直接影响 DT-1/DT-2 的结论

1. DT-1 不重建 DE430/WMM/J2 的既有能力；先审计其数据、坐标、时间和实际整星消息链，再增加 NRLMSISE、J3/J4 和 SRP/阻力统一耦合。
2. DT-2 不重复造 RW、IMU、星敏、磁强计和 CSS 基础模型；先核实现有 Basilisk 模块、项目 SysModel、故障事件和随机种子路径。
3. 对任何 2.11.0 已知传感器噪声问题，优先选择版本升级/官方修复验证，不用 Python 后处理噪声伪装成原生传感器噪声。
4. 推荐整星产品路径与 BSK-RUN/BSK-6DOF 实验性路径必须在 Capability、UI、Run Bundle 和报告中继续显式区分。
5. 高保真声明以任务容差和独立 V&V 为前提；不能仅因使用 Basilisk、DE430 或 WMM2025 就提升置信度。

## 7. 主要源码与资料索引

- 推荐整星能力：src/sat_sim/capabilities/whole_spacecraft.unified_native.v1.yaml
- 推荐整星轨道环境：src/sat_sim/bsk_engine/orbital_environment.py
- 独立 Basilisk 轨道扩展：src/sat_sim/orbit/basilisk_hf.py
- 轨道扩展基准：src/sat_sim/benchmarks/basilisk_orbit.py
- 6-DOF 候选：src/sat_sim/spacecraft/basilisk_6dof.py
- ADCS FSW 候选：src/sat_sim/adcs/basilisk_fsw.py
- 单机能力边界：docs/engineering/p9-n-component-capability-inventory.md
- 实时性能预算：configs/interactive/performance_budget.json
- Run Bundle：src/sat_sim/run_bundle.py
- 参数溯源验收：docs/engineering/parameter_traceability_acceptance.md
- 校准流程：docs/标定配置指南.md

## 8. 版本与依赖元数据审计发现

实验性 orbit_environment.basilisk_hf.v1 capability YAML 当前声明 optional_dependency 为 bsk==2.10.2；而当前项目主要真实整星/浏览器验收基线使用官方 bsk==2.11.0，whole_spacecraft.basilisk_6dof.v1 也声明了不同的 2.11.0+satfix2 版本边界。

这属于 capability 元数据与当前执行基线之间的版本口径不一致。它不证明运行代码必然不兼容，但会误导用户和自动化环境选择。

**处理要求：** DT-1 在推广 BSK-ORB 实验性路径前，先核对官方 Basilisk 2.11.0 的真实 API/模块可用性、capability optional_dependency、工作流安装版本和实际运行产物；统一后再更新 capability 元数据。不得通过仅改 YAML 版本号来代替运行验证。
