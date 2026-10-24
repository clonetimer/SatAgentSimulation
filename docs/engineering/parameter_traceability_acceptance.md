# P0｜单机、分系统与整星可配置参数追溯门禁

本文件定义的是**覆盖审计及整改工作流**，不是“全部参数已通过 Basilisk 物理验收”的声明。

## 审查的两种运行模式

- **有限时长单次仿真**：各独立 Capability 通过 Registry 的 parameters 字段生成 Schema 表单。只有标记为需要 Basilisk 的具体能力，才可主张在其受限功能边界内使用 Basilisk 原生执行。项目 source-native 部件（例如独立 RW、太阳板、CMG）不可因为有参数表就自动宣称它们的执行内核是 Basilisk。
- **整星持续仿真**：单独使用 continuous_initial_config.py 列出的运行前配置字段，最终送入 UnifiedNativeRuntime 的原生混合模型图。只有统一原生整星的 continuous=True 是正式持续模式；独立分系统的有限时长交互并不是同一回事。完整跨进程物理恢复没有通过验收。

**登记口径特别说明：**分系统 YAML 文件共 18 个，但当前 active 且 exposed_to_agent 的现役可选分系统只有 12 个；其余 6 个需在报告中保留并标注生命周期。具备 schema、能够生成表单，并不代表默认 UI 应当提供运行入口。全部 24 个独立部件合同当前均为现役能力，但执行内核仍需分别识别是否依赖 Basilisk。

## 六级逐参数验收

| 级别 | 证据 | 当前工具能否证明 |
|---|---|---|
| L1 声明 | Capability YAML 参数、类型、单位、默认值 | 能 |
| L2 录入 | 由相同 Registry 生成的前端 Schema 字段 | 能；自动化测试阻断遗漏 |
| L3 转译 | TaskSpec 经 Adapter 到物理模块的字段映射 | 不能自动证明；需分模块追踪 |
| L4 接线 | 实际 Basilisk 类属性、消息订阅、effector 挂接 | 不能自动证明；需原生代码和模型图证据 |
| L5 生效 | 对单个参数做 A/B 扰动，原生内部状态或输出产生物理上合理变化 | 不能由静态 AST 证明；需要真实 Basilisk 测试 |
| L6 制品 | 证据包含参数值、原生模块来源、时间序列、原始运行包和数值验收 | 不能由表单测试证明；需要 Run Bundle 验收 |

**准入原则：** L1/L2 PASS 只代表“已登记且可编辑”，绝不是 L3–L6 PASS。

## 可重复执行

Windows Git Bash：

    PYTHONPATH=src:. python -m pytest -q tests/test_parameter_surface_audit.py
    PYTHONPATH=src:. python -m sat_sim.validation.parameter_surface_audit

Windows PowerShell：

    $env:PYTHONPATH="src;."
    python -m pytest -q tests/test_parameter_surface_audit.py
    python -m sat_sim.validation.parameter_surface_audit

输出在：

- reports/parameter_surface_audit/coverage.json：逐 Capability、逐参数 JSON 矩阵，以及原生源码和持续初始配置的差集
- reports/parameter_surface_audit/coverage.md：便于评审的表格和待确认键清单
- GitHub Actions 的 parameter-surface-audit 构件：每次对能力参数、表单或原生运行代码的 PR 改动会重算并保存

该审计通过 AST 查找整星原生源文件中的 values.get(...) / values[...] 字面量引用及动态 f-string 前缀，避免把注释、文档和字符串误判为参数使用。**它不进行跨文件 Adapter 数据流追踪、Basilisk 安装/执行或物理扰动测试。** 只对 Registry 和生成表单结构一致性启用阻断；原生未注册/未读取键均作为整改清单，保留待评审状态。由于底层可能存在内部默认值及多条实现分支，缺口数量不能直接等于物理故障数。

## 首批已开放的持续模式原生参数（P0-1）

- spacecraft_mass_kg：在运行前的“整星质量与轨道初始条件”分组设置，直接写入官方 Basilisk Spacecraft.hub.mHub；注意整星惯量矩阵当前仍为模型固定假设，修改质量并不会自动按几何体重算惯量。
- sun_sensor_fov_deg：在 ADCS 分组设置，角度由度转弧度后写入官方 Basilisk CoarseSunSensor.fov；它仅控制太阳敏感器自身的视场，不等于磁强计或星敏视场。
- 这两个字段在原生整星初始化中原已有实际消费路径，本轮只是增加严格限制的运行前输入。默认值保持与既有模型一致，省略字段不会改变旧场景。
- 验收包括非法边界阻断、REST 配置透传，以及运行中的真实 Basilisk 对象属性检查。此证据不是完整动态扰动、任务级性能或中断恢复一致性验收。

## P0-2 原生反作用轮摩擦及安装（整星持续运行）

- 整星持续仿真创建表单中现有 **质量和太阳敏感器视场** 字段已真正补进 HTML；增加静态 HTML ↔ 原生配置目录全字段集合对比，避免“API 有参数但用户看不到”的回归。
- 在 ADCS 运行前配置新增轮组共享的 `rw_friction_mode`、`rw_coulomb_friction_nm`、`rw_viscous_friction_nms` 和 `rw_mount_radius_m`。源内核经严格范围检查后交给 **官方 Basilisk rwFactory.create**；轮安装向量按当前已选择的轴系乘以共同半径（正交三轮/金字塔四轮各自计算）。
- `disabled` 模式下摩擦系数必须为 0，默认保持原有不施加摩擦行为；`coulomb_viscous` 模式至少一个摩擦系数大于 0，范围分别为 [0, 0.01] N·m 和 [0, 0.01] N·m·s/rad。当前设置不是单轮差异化故障、Stribeck 标定或真实结构安装几何拟合。
- 安装半径只能在 [0, 0.25] m 范围变化；采用向轮轴方向平移各个安装点的**简化几何规则**，不等于任意三维安装坐标/独立安装姿态。
- 真实 Basilisk 测试通过透明 factory 监视器检查实际给原生部件传递的 `rWB_B`、`useRWfriction`、`fCoulomb` 和 `cViscous` 参数，并让官方 Basilisk 继续完成动力学；这属于构造/接线证据，**不等于已获得摩擦衰减曲线标定或硬件级精度认证**。

## P0-3 IMU 与星敏感器安装配置（统一整星持续模式）

- 新增 **9 项 ADCS 单机安装参数**：IMU 三个 3-2-1 安装角（yaw/pitch/roll）、IMU 在机体系 B 中的 XYZ 位置，以及星敏感器机壳系 C 相对机体系 B 的三个 3-2-1 安装角。
- 所有角度由用户在网页按 **度** 输入（航向和横滚 −180° 至 180°，俯仰 −89° 至 89°）；位置参数为机体系 **米**（−2 至 2 m）。初始化转成弧度，分别写入官方 `ImuSensor.setBodyToPlatformDCM(yaw,pitch,roll)`、`ImuSensor.sensorPos_B` 和 `StarTracker.dcm_CB`（后者由 Basilisk `RigidBodyKinematics.euler3212C` 生成）。
- 默认安装角和位置均为零，保持前版真实 Basilisk 计算图的默认姿态与位置；只允许创建新会话前配置，不开放运行中随意改安装参数。
- 强制的 HTML ↔ 后端 Catalog 逐字段覆盖门禁防止漏配；REST 传参、非法范围阻断与真实 Basilisk C++ 模块安装矩阵／位置检查由专项测试覆盖。
- **边界：**星敏、IMU 的硬件噪声参数、误差传播矩阵、偏置、饱和、频率、视线遮挡以及动态精度标定仍待后续单独评审；不将本次安装几何参数视为全部传感器模型完整。
- 注意 Basilisk 传感器惯用安装角度和 `dcm_CB` 是机体系到各自传感器坐标系的变换；不能把机体初始姿态和传感器安装姿态混为一谈。

## P0-4 原生 IMU 恒定偏置、量程及星敏正常噪声（持续整星）

- 本轮新增 9 个 ADCS 运行前字段：IMU 三轴陀螺恒定偏置（rad/s）、三轴加速度计恒定偏置（m/s²）、陀螺最大量程（rad/s）、加速度计最大量程（m/s²）、星敏正常工况的逐轴噪声标准差（arcsec）。各项经 Pydantic 严格限制，所有默认值均为零。
- 对 IMU，分别接入官方 `Basilisk.simulation.imuSensor.ImuSensor.senRotBias` 与 `senTransBias`；量程仅在设置为正值时写入 `senRotMax` 和 `senTransMax`，**0 的语义是保留原生默认量程，并非量程为零**。未设置时继续保留旧模型行为。
- 星敏正常工况噪声：将角秒转换为弧度，设置官方 `StarTracker.PMatrix` 的对角线为标准差、非方差；同时显式设置零 `AMatrix` 实现白噪声，不受历史 Basilisk 版本中默认传播矩阵差异影响。星敏默认噪声关闭，非零时保留当前原生输入/输出连接。故障事件里的 `adcs_star_tracker_accuracy_loss` 仍是另一套独立注入机制，不会冒充原生传感器噪声。
- 原有 `ImuFaultInjector` 仍负责事件触发的阶跃偏置/噪声增强；本轮添加的是 **官方传感器本身的正常测量偏置与限幅**。这两个链路串联，启用两种误差时结果会叠加，使用者需要分别记录配置。
- 真实 Basilisk 测试用官方 SWIG 模块读取属性与矩阵，验证界面、API 原样传入传感器；没有将正常传感器噪声改造成纯 Python 随机数仿真。
- **待补项：** 正常 IMU 白噪声、偏置随机游走、漂移、量化、逐轴饱和、星敏误差状态边界与传感器误差下的闭环性能/统计标定；完成这些之前不能声称传感器参数工程级齐全。跨进程完整恢复也不属于本轮验收。

## P0-5 持续整星推进与热控参数首批开放

- **Basilisk 推进：**运行前可选 `propulsion_enabled`（默认关闭）；启用后复用真实 `ThrusterDynamicEffector`、`FuelTank`、`Spacecraft` effector 挂接和 Basilisk 消息驱动的点火控制。新增每只推进器推力、比冲、点火时间/持续时间、燃料箱初始质量/容量、最小点火 SOC，以及与原生电池消息耦合的项目侧电功率估算。启用推进并不表示一创建会话就立即点火；要到 `propulsion_burn_start_s` 时刻且满足许可条件才会燃烧。
- **项目热网络：**新增载荷、通信、推进热节点的初始温度，以及通信—结构、推进—结构两条导热路径系数。**这部分不是 Basilisk 官方原生热模块**，而是运行于 Basilisk 调度中的项目热网络，继续有初始温度、热容量、辐射、热沉、加热器、导热路径和热安全状态。
- **输入约束：**Pydantic 禁止未知字段、非有限值和越界值；燃料初始量不得超过容量。热节点温度界限分别遵照已有 `ThermalNodeParams` 的安全范围，并保证当前默认值与前版物理图保持一致。网页复选框必须提交 JSON boolean，不得将 `"on"` 数值化为 NaN。
- **原生检查：**由真实 Basilisk 回归透明捕获官方推进图构建结果及其中的推力、Isp 和燃料配置；同时核对热网络的节点初值与导热路径实际配置。现有连续动力学、耐久、TC/TM 与静态前端一致性门禁全部保留。
- **未验收：**推进器逐台安装坐标、喷流扰动、阀门响应、电源瞬态、硬件级推进推力精度、热网络辐射视角因子/有限元热模型、独立分系统真实连续运行、跨进程恢复一致性；不得将新增配置数等同参数工程完整性。

## P0-6 双推进器原生安装几何与最小脉宽

- 将两台实际接入 `ThrusterDynamicEffector` 的推进器 T1、T2 分别开放 `direction_b_xyz` 机体系推力方向和 `location_b_xyz_m` 安装坐标。方向必须为单位向量（平方范数在 [0.999,1.001]）；位置单轴范围 [−2,2] m。默认两者沿 +X_B，位置分别为 (0,+0.5,0) m 和 (0,−0.5,0) m，与此前运行图一致。
- 新增 `propulsion_min_pulse_s`，映射项目 `ThrusterCommandConfig.min_pulse_s`，再转译成**官方 Basilisk ThrusterFactory 的 `MinOnTime`**。默认 0.05 s；该量是执行机构最小脉冲，不是点火计划 `propulsion_burn_on_time_s`。
- 字段同时登记在有限时长统一整星能力合同，以及持续运行前 Pydantic 严格配置和网页，当前硬件拓扑固定为两台；不得把页面的两个输入组解释成任意增删推进器数目的能力。
- 验证按六级分开：Registry 和网页字段一致性、API 拒绝非单位方向和越界坐标、原生 Basilisk 推进器与燃料箱装配图的真实 `BasiliskThrusterSpec` / `MinOnTime` 接线值；最后在真正的 Basilisk 整星执行中进行两套相同推力/比冲而方向不同的 A/B 仿真，比较有燃烧证据的燃料用量及轨道响应。
- **证据边界：**该 A/B 只是定性行为生效检查，不是精度标定；质心随燃料变化的几何修正、推力不对中统计、阀门响应、喷流相互作用和跨进程整星状态一致性尚不在这一阶段。

## P0-7 项目热网络热容量、辐射及电池加热器配置

- 在统一整星有限时长 TaskSpec 与持续运行前界面均增加 6 项热控配置：载荷节点质量（默认 3 kg）、比热容（600 J/(kg·K)）、发射率（0.75）、载荷散热器面积（0.25 m²）、电池加热器功率（12 W）、电池加热器设定温度（286 K）；另保留既有三节点初始温度和两条结构导热系数。
- 物理模型边界：此处热控是挂在 Basilisk 任务中的**项目确定性 ThermalNetworkSysModel**，不是 Basilisk 官方原生热动力学。它按真实净热流和质量×比热容积分温度，导热、热辐射、辐射器、加热器均使用原有模型对象；不能宣称工程热控有限元/硬件级精度。
- 验收分级：注册表和表单字段一致、参数类型/单位/范围约束及禁止任意未知字段；REST 保存不可变配置；透明获取真实热网络节点 `ThermalNodeParams`、`RadiatorParams`、`HeaterParams` 的实例值；在 Basilisk 原生调度下执行**两种热容量的 A/B 温度曲线**，确认 15 kg 载荷在与 3 kg 相同工况下响应变缓。
- 默认参数不变；散热器面积和加热器功率/阈值尚需各自的量化扰动验收，温度响应 A/B 只是项目热网络的部分验收。热视角因子/太阳跟踪热耦合、热传导完整拓扑编辑、控制器 PID、各单机热控独立持续运行、整星跨进程恢复一致性继续列为缺口。

## P0-7 热控散热器与加热器的 Basilisk 调度下物理 A/B

- 同一项目 `ThermalNetworkSysModel` 使用官方 Basilisk `SimulationBaseClass.SimBaseClass` 建立调度任务并 `ExecuteSimulation()`；不使用纯 Python 简化积分器，也不宣称热网络为官方 Basilisk 原生热部件。
- 散热器：设定相同载荷节点初温 320 K、相同热网络和日食条件，在散热器控制阈值允许部署的工况下，对比 0.1 m² 与 0.8 m² 面积。验收**散热器实算排热功率增加和载荷最终温度降低**；该测试隔离热源，避免整星安全模式开关污染因果判断。
- 电池加热器：设定相同 285 K 节点初始温度、30 W 额定加热功率与同等热边界，仅改变设定温度 275/295 K。验收低设定值下不启动、高设定值下启动，记录加热功率及最终温度响应。
- 以上是**已实现热模型内部**的因果一致性验证，不构成热真空试验或热容/辐射率的工程级物理精度认证。热网络拓扑、辐射视角因子、各热节点设备级热负载及多物理耦合仍列入待办。

## P0-8 太阳电池板机体系固定法线（Basilisk SimpleSolarPanel）

- 有限时长统一整星 TaskSpec、持续运行前 EPS 表单及 `native_values` 新增 `solar_normal_b_x/y/z` 三轴。默认值 (1,0,0) 保持旧模型不变；每轴限制在 [-1,1]，向量模长平方限定 [0.999,1.001]。持续模式由 Pydantic 交叉字段校验，有限时长模式由执行时的原生模型边界再校验（包括 NaN、Inf）。
- 三轴直接送入**官方 Basilisk SimpleSolarPanel.setPanelParameters(normal_B, area_m2, efficiency)**；保留现有 `sunInMsg`、`stateInMsg`、`sunEclipseInMsg` 及经 `nodePowerOutMsg` 接入 `simpleBattery` 的原生消息路径。
- `normal_B` 是机体系**固定安装法线**，不是姿态控制指令、太阳跟踪关节或旋转速度。独立部件和简化 EPS 中的追踪功能不因此扩展到统一原生整星。
- 验收测试对同一原生整星配置只切换 +X_B 和 -X_B 面板法线，使用实际 Basilisk 计算太阳板发电量，验证面对和背向太阳时的差异。此测试不等于太阳跟踪、遮挡热耦合或硬件标定完成；项目日照仍使用现有解析太阳历表。

## P0-9 可选限速太阳跟踪（项目 SysModel + 官方 Basilisk SimpleSolarPanel）

- 在有限时长统一整星 TaskSpec 和持续整星运行前 EPS 配置增加 `solar_tracking_enabled` 与 `solar_tracking_max_slew_rad_s`。默认关闭且速度为 0，保持之前的固定法线行为；启用时速度必须在 (0,1] rad/s，关闭时必须为 0；持续配置和直接有限时长执行均做双重失败关闭校验。
- Basilisk 调度任务中增加可选 **项目运动学 SysModel**，按 SCStates 与 Sun SpicePlanetState 消息计算机体系太阳方向，按不超过 `slew_rate × Δt` 的球面最短弧移动面板法线，随后在当前任务内更新官方 SimpleSolarPanel 的方向。调度优先级在参数故障控制器（58）之后、官方太阳电池板功率更新（55）之前（控制器 57），以保留效率故障和最终官方功率消息流。
- 模型类别严格区分：太阳板功率、余弦和 Eclipse 遮挡、电池充放电仍由官方 Basilisk 模块负责；转轴跟踪是**项目运动学控制器**，没有太阳翼铰链状态、质量惯量、电机电流/功率、轴承摩擦、柔性模态或反作用力矩，不能宣称工程级可展开太阳翼机构。
- 回归门禁要求真实 Basilisk 全整星 A/B：相同初始背向太阳法线，未启用跟踪时功率保持近零，启用限速跟踪后功率上升；从真实调度控制器采集逐步转角并逐步验收限速、单位法线、运行电能边界。保持原有 +X / -X 固定法线 A/B 回归。
- 后续仍须实现具有真实状态/反力矩的关节动力学与驱动执行机构、面板多片几何及自遮挡；跨进程完整物理恢复未通过验收，不能因为跟踪配置增加而宣称已具备。

## P0-10 官方 Basilisk Spacecraft 主惯量独立配置

- 整星有限时长 TaskSpec 与持续运行前的 Orbit 表单分别增加 `spacecraft_inertia_xx_kg_m2`、`spacecraft_inertia_yy_kg_m2`、`spacecraft_inertia_zz_kg_m2`。默认仍为 [100,80,60] kg·m²；每个正值不超过 10000 kg·m²，三轴必须满足严格刚体主惯量三角不等式。
- 每项直达官方 Basilisk `Spacecraft.hub.IHubPntBc_B` 对角矩阵，而非变更数据展示/估算代理；初始质量 `spacecraft_mass_kg` 和主惯量为**独立模型参数**，更改质量不会自动推导惯量。
- 仅支持在机体系 B 中对角惯量矩阵，不能视为任意惯性积、质量分布 CAD 自动计算或推进耗燃料的惯量动态修正。默认图不变；对有限 TaskSpec 在真正创建 Basilisk 刚体对象时再次做非有限值、范围和三角关系 fail-closed 检查。
- 验收门禁包含 UI↔Registry ↔ Pydantic 映射、无效配置拒绝、官方 SWIG Spacecraft 原生对象的实际三轴惯量矩阵，以及相同初始状态与相同 FSW/RW 下主惯量改变的实际 Basilisk 姿态/角速度曲线 A/B；不将静态矩阵检查单独冒充 L5 物理生效证明。

## P0-11 原生 Honeywell HR16 逐轮额定动量容量

- 整星有限时长 TaskSpec 与持续运行前 ADCS 配置中新增 `rw_max_momentum_nms_0..3`。每个真实反作用轮只能选择 Honeywell HR16 支持的 **50/75/100 N·m·s** 档位；RW1~RW3 默认 50，RW4 仅适用于 `pyramid_4`，该档位默认 50。网页在三轮构型隐藏/禁用 RW4 字段；三轮对 RW4 的显式输入直接失败关闭。
- 在统一原生图中，每轮单独传给官方 `Basilisk.utilities.simIncludeRW.rwFactory.create("Honeywell_HR16", ..., maxMomentum=...)`。该额定动量容量对应官方 HR16 工厂的轮质量、`Js=H/Omega_max` 转子惯量（`Omega_max=6000 rpm`），而非仅改变命令夹持器或遥测描述。离散取值源于上游硬件型号工厂，只允许三档；非法值在任何上游模块可能退出进程前先拒绝。
- 新的 Basilisk CI 物理门禁在同一整星闭环中对 RW1 的 50/100 两档做 A/B：捕获真实官方工厂返回的 RW 对象的 `Js` 与 `Omega_max`，确认仅 RW1 惯量翻倍、RW2/3 不变；随后确认实际 Basilisk 采集的 `adcs.rw.speed_rad_s_0` 轨迹发生差异。补充四轮 [50,75,100,75] 实装检查、非法档位与虚拟 RW4 拒绝。
- 不等同于为每只轮自由设定独立电机力矩、轴承摩擦、偏置安装轴或任意品牌型号；之前的轮组共享摩擦和最大力矩限制原样保留。整星跨进程完整物理恢复仍未通过。

## P0-12 原生反作用轮逐轮摩擦系数

- 冻结 Vizard 新增工作，优先 P0 参数整改。轮组摩擦增加兼容性模式 `per_wheel`，保留此前 `disabled` / `coulomb_viscous` 的默认值与全部语义。
- 新增 RW1–RW4 独立 `rw_coulomb_friction_nm_0..3`（N·m）及 `rw_viscous_friction_nms_0..3`（N·m·s/rad），取值 [0, 0.01]；RW4 只允许 4 轮金字塔构型，三轮下前端禁用且后端拒绝显式指定。启用 `per_wheel` 必须至少一轮系数非零，且统一轮组系数必须为 0；独立系数不得偷偷混入其余两种模式。
- 逐轮系数分别进入真正 `Basilisk.utilities.simIncludeRW.rwFactory.create(..., useRWfriction, fCoulomb, cViscous)`，每轮根据其独立零/非零系数配置真实轴承摩擦，绝非伪造的遥测或事后添加噪声。原生 C++ `ReactionWheelStateEffector` 继续连接姿态动力学与 FSW。
- L5 验收：官方 SWIG RW 对象参数与 factory 参数双方核验；相同轨道、初始姿态与控制器，单独改变 RW1 摩擦应导致真实 Basilisk `adcs.rw.speed_rad_s_0` 曲线差异。补充 4RW 独立组合和原有共用模式的回归、非法/非有限值/不存在 RW4 的直接 TaskSpec 和 Pydantic 拒绝测试。
- 这**不等同于**每只轮的自由可配置电机力矩、温度依赖磨损、结构安装三维位姿或转子振动；整星跨进程精确恢复仍为 BLOCKED。Vizard P1~P3 明确等待参数整改阶段后再考虑。

## P0-13 官方 Basilisk 反作用轮逐轮电机最大力矩及故障指令一致性

- 继续优先 P0 参数整改；不扩展 Vizard。新增 `rw_max_torque_nm_0..3` 为可选逐轮电机限幅，允许 (0,0.2] N·m。保持旧 `rw_max_torque_nm` 统一值作为没有逐轮覆盖时的后备值，默认始终为 0.2 N·m。RW4 只在四轮构型存在；前端需要显式勾选才能启用独立参数编辑，默认不改变旧场景。
- 实体级验证：每轮独立参数传给官方 `Basilisk.utilities.simIncludeRW.rwFactory.create(..., u_max=...)`，改变 C++ 反作用轮的真实扭矩能力。与此同时，Basilisk 任务中的 `ReactionWheelCommandFaultManager` 必须使用完全一致的逐轮限幅，禁止用统一限幅覆盖较小的物理上限；失效降额、RW 卡滞和超速制动按目标 RW 自身上限计算。
- 遥测 `adcs.rw.effective_max_torque_nm_0..3` 为实时故障限幅，`adcs.rw.effective_torque_ratio_0..3` 按对应轮的原生硬件额定值归一化，不能统一除以 RW1 或共享额定值。
- L5 原生 A/B：真实 Basilisk factory 所返回轮对象的 `u_max` 为 [0.0001,0.12,0.15] N·m，实际 `adcs.control.applied_torque_nm_*` 永不超过对应轮的限幅；RW1 降额相对统一默认 0.2 N·m 的真实轮速轨迹必须改变。另校验四轮 [0.07,0.16,0.16,0.03]、共享历史后备、非法参数与不存在 RW4 的 fail-closed。
- **界限**：该项只配置扭矩上限，不提供每轮直接任意扭矩指令、驱动电机效率曲线或动态速度-力矩包络。整星跨进程精确状态恢复继续 BLOCKED。Vizard P1～P3 继续冻结直至参数整改收敛。

## P0-14 反作用轮逐轮三维安装几何（官方 Basilisk）

- 参数整改优先，不扩展 Vizard。新增可选 `rw_geometry_mode=per_wheel` 和 RW1～RW4 的 `rw_mount_b_{x,y,z}_m_{0..3}`（机体系 B 中实际轮质心位置，±0.25 m）及 `rw_axis_b_{x,y,z}_{0..3}`（单位自旋轴）。共 25 个整星原生配置项。默认 `legacy_axis_offset` 完整保留预设三轮正交/四轮金字塔方向与 `rw_mount_radius_m` 的原始语义；不存在的 RW4 严格拒绝。
- 不允许旧统一偏置半径与逐轮安装位置混用；逐轮模式要求至少一项实际输入，自定义轴三个坐标必须同时给出且长度平方在 [0.999,1.001] 内。通过任意三轴列组成矩阵的行列式阈值（绝对值 ≥0.05）保障至少存在三轴可控的几何结构，避免退化控制矩阵进入原生 FSW。
- 由共享 `bsk_engine.rw_geometry.resolve_rw_geometry` 同时校验连续前启动配置和直接整星 TaskSpec；三轴位置与方向最终进入官方 `simIncludeRW.rwFactory.create(gsHat_B, rWB_B=...)`，轮组惯性耦合仍由官方 `ReactionWheelStateEffector` 求解，绝非独立 Python 几何代理。
- **Basilisk 2.11 BalancedWheels 质量属性边界**：官方 [Issue #600](https://github.com/AVSLab/basilisk/issues/600) 明确指出旧模型默认忽略 RW 的恒定质量／惯量贡献，假定已经合并在 Hub 质量属性中，所以单独改变 `rWB_B` 时旧模型角速度轨迹不会变。这是实测物理缺口，禁止仅以 SWIG 入参正确假装 L5 通过。对显式逐轮模式中非零安装位置，本工程按“旧 Hub 质量／惯量已包含 RW 在 B 原点的质量属性”这一建模契约，从官方 RW 对象读取 `mass`，采用**增量平行轴惯量与总质心偏移**重新设置官方 `Spacecraft.hub.IHubPntBc_B`、`r_BcB_B`，但**不再次加 RW 质量到 `mHub`**。要求每轮质量为正且其和小于整星总质量、最终惯量正定且满足主惯量三角关系，否则拒绝运行。该补偿是项目级质量装配接线，真正的 6-DoF 运动仍由 Basilisk 原生 Hub/RW 求解，不等于上游 BalancedWheels 自动质量记账。
- 真实 L5：捕获 Basilisk 官方 RW 设备的 `gsHat_B` 与 `rWB_B` 值，分别验证**仅改变自旋轴**、**仅改变安装点**时，在相同 FSW 与初始条件下真实角速度轨迹分叉；纯位置变更还需验证官方 Hub 质心和惯量确实变化。四轮非对称几何与非法数值、非单位轴、缺少坐标、秩退化、三轮输入 RW4，以及 RW 质量预算非法都须失败关闭。
- 现阶段**不提供**任意三维转动组件动平衡参数、RW 轴陀螺力矩依赖温度或装配有限元，不代表自动更新 `Spacecraft.hub.IHubPntBc_B` 以反推卫星 CAD 惯量。跨进程物理精确恢复仍 BLOCKED，Vizard 后续扩展继续延后。

## P0-15 原生 IMU 测量噪声、随机种子与有限整星登记

- **保真度界限**：以前 `gyro_base_noise_std_rad_s` 仅控制项目级 `ImuFaultInjector` 在噪声故障事件发生时的增益，不是官方 `Basilisk.simulation.imuSensor.ImuSensor` 的正常测量噪声；不得把故障注入噪声当作正常硬件白噪声。其余已接线 IMU 量程/安装/偏置及星敏噪声此前缺少整星 TaskSpec 注册，不允许仅凭网页字段数量宣称闭环。
- 原生新参数 `imu_gyro_white_noise_std_rad_s`（三轴共同标准差 0～0.1 rad/s）和 `imu_accel_white_noise_std_m_s2`（0～1 m/s²）直写官方 SWIG `PMatrixGyro`、`PMatrixAccel` **对角矩阵**；矩阵值为 sigma（协方差平方根），非 sigma²。正值下强制 `setAMatrixGyro(0)` / `setAMatrixAccel(0)`，避免固定 Basilisk 2.11 陀螺 A 默认随机游走与“白噪声”用户设定矛盾。两个参数默认 0：保持历史完全无官方随机扰动。
- 新增可复现 `simulation_seed`（0～2³¹−1 整数）供官方 IMU、星敏及原有项目故障注入使用；前启动强类型检查和直接 TaskSpec 双重 fail-closed。注意旧 2.11 的 IMU gyro 与 accel 噪声流独立性不足；官方 2.12 才修复不同种子派生策略，因此当前不能声称两种噪声独立或跨不同编译器逐点一致。
- IMU 既有陀螺偏置 3、加速度计偏置 3、两项饱和量程、安装 Euler321 3、B 系安装位置 3 以及星敏噪声 1，也同步注册到有限整星原生 capability 表。整星有限参数登记净增 18，持续运行前表单净增 3。
- 将真实上游 `imu.sensorOutMsg` 新增到行级证据 `adcs.sensor.raw_gyro_rad_s_{x,y,z}`、`adcs.sensor.raw_accel_m_s2_{x,y,z}`，以便区分官方测量结果与经过项目事件注入的 `adcs.sensor.gyro_measured_rad_s_*`。
- L5 验收：真实 Basilisk SWIG ImuSensor 的 `PMatrix*/AMatrix*`、随机种子，原始 IMU 三轴测量在非零噪声配置下相对零噪声基线确实改变，同种子重跑可复现、不同种子应改变测量；无故障事件时下游应与原始输出一致。并核对噪声禁用旧场景、非法值与直接有限 TaskSpec 的失败关闭。
- 不含 Allan 方差标定、噪声功率谱密度随采样间隔转换、轴间相关性、温度漂移、各轴独立 sigma 或噪声积分模型，均另列高保真候选；不会误认为本轮新增传感器测量噪声就完成 IMU 全部工程参数整改。
- Vizard 新功能继续暂停，优先剩余 ADCS/其他分系统参数。

## P0-16 磁强计原生三轴安装角与标定参数

- 原有整星调用链把官方 `Basilisk.simulation.magnetometer.Magnetometer` 的 `setBodyToSensorDCM(0,0,0)` 固定写死，未向用户提供安装参数。现新增 `magnetometer_mount_{yaw,pitch,roll}_deg`（3-2-1 B→S 欧拉角）、`magnetometer_bias_tesla_{x,y,z}`（S 系逐轴常值偏置 ±1e-4 T）和 `magnetometer_scale_factor`（无量纲 (0,2]），7 项在有限 TaskSpec 与持续前端都有准确的默认值和单位。
- 这些参数由统一原生图直接写入官方 C++ Magnetometer 的 `setBodyToSensorDCM`、`senBias` 和 `scaleFactor`，所有上游输入仍为真实 Basilisk `MagneticFieldCenteredDipole.envOutMsgs` 与 `Spacecraft.scStateOutMsg`；下游 ADCS fusion 不改为模拟场。直接 TaskSpec 的非有限值／超界／错误类型必须在 SWIG 前失败关闭。默认 (0,0,0)、(0,0,0) T、比例 1 保持历史行为。
- 真实 L5：在相同轨道、姿态和仿真种子下，比较官方 TAM `adcs.magnetometer.tesla_{x,y,z}`：仅安装角变化须符合官方 `dcm_SB` 对原场向量的旋转，仅偏置变化须以特斯拉计入传感器输出，仅比例变化须对原始实测值产生比例响应；运行中捕获 SWIG 原生磁强计对象确认几何与标定配置，不能仅验证前端字段数量。
- Basilisk 2.11 已知 `senNoiseStd` 默认传播矩阵和 RNGSeed 应用存在问题，官方 2.12 才修复，因此此 PR **不冒充已实现磁强计白噪声**，更不把错误的随机游走视为默认测量白噪声。噪声标准差／功率谱、随机偏置漂移、三轴交叉标定和饱和仍另列模型/版本修复清单，避免替换真实数据源。
- Vizard 继续冻结，整星精确跨进程恢复与依赖安全发布均仍分别 BLOCKED，不因新增传感器参数而变更状态。

## P0-17 官方 Basilisk CoarseSunSensor 安装法线与有限整星视场补登记

- 原来统一整星在 `CoarseSunSensor.nHat_B` 固定使用 `[0,0,-1]`，用户不能设置太阳敏感器在机体系 B 的独立安装方向；这与 EPS 太阳电池板的 `solar_normal_b_*` 是两个不同的实体，必须保持互不影响。
- 新增 `sun_sensor_normal_b_{x,y,z}`（各分量 [-1,1]，机体系无量纲），默认严格保留 `(0,0,-1)`。一旦指定安装方向，**三个分量必须全部指定**，真实矢量长度平方在 [0.999,1.001]；数值非法、非有限、布尔类型或退化轴必须由共享 `resolve_sun_sensor_boresight` 同时在持续仿真 Pydantic 与有限 TaskSpec 运行时失败关闭。不得简单归一化用户无效输入。
- 三轴法线直接写入**官方 C++** `Basilisk.simulation.coarseSunSensor.CoarseSunSensor.nHat_B`，并以 `cssDataOutMsg.OutputData` 作为原生正余弦／视场观测；不以 Python 模拟太阳矢量冒充其测量。已有 `sun_sensor_fov_deg` 写入真正的 `fov`，但此前未注册为有限整星参数，本轮补登记，使用角度范围 (0,180] 度且在 TaskSpec 校验后换算弧度。
- 真实 L5：同轨道、同姿态和时刻，仅将法线从 +X 反转到 -X，官方 CSS 输出必须显著不同；太阳电池板物理发电和项目独立太阳矢量遥测必须保持一致。指定偏轴法线时，缩小 `fov` 必须使真正的 CSS 测量失效，并检查官方 SWIG 属性与时间曲线一致。旧默认 -Z、三轴单位长度、FOV 非法和部分分量丢失均单独测试。
- **模型边界**：此功能只建模一个 CoarseSunSensor 的单轴视场方向，**不**实现多探头阵列、遮挡/结构自身阴影、失准随温度变化、精确光电电流噪声 PSD；项目 `SunDirectionSensor` 是与官方 CSS 分开的独立遥测信号，并非等价硬件。跨进程物理精确恢复仍 BLOCKED。Vizard P1～P3 继续冻结，参数整改优先。

## 后续 P0 闭环顺序

1. **真实原生 ADCS**：确认 RW 安装轴、位置、最大动量、摩擦与电机电流/力矩等值最终写入官方 reactionWheelStateEffector 配置；IMU 与星敏安装、噪声、偏置及数据连接逐项验证。明确独立 source-native 部件与统一 Basilisk 内核的区别。
2. **EPS/太阳板**：统一 power node 的面板法线、跟踪、遮挡、功率输出及电池耗能证据；区别单机 builder 可选能力与整星实际调用路径。
3. **持续整星初始化**：现有只按 Orbit/ADCS/EPS/Payload/CommData 五组开放字段，后续补热控、推进及必要的固定安装参数，必须先证明值从 API 经过 native_values 进入实际原生图。不得为增加字段放松 Pydantic extra=forbid。
4. **其他单机及分系统**：对 MTB/CMG/磁强计、通信链路、载荷、热网络、推进开展相同的数据流及扰动门禁；项目简化模型仍保留，但 UI 显示真实保真度边界。
5. **发布**：参数审计 JSON 与真实 Basilisk 差异试验、Run Bundle 制品同步存档；没有动态证据的字段只能标为配置候选。

现有的 native_capability_audit.py 负责更细的 Basilisk API/字段映射分类；本报告负责面向用户操作的 Registry→表单→运行源字段差集，两者相互补充，不相互替代。
