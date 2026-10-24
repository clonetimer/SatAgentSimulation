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

## 后续 P0 闭环顺序

1. **真实原生 ADCS**：确认 RW 安装轴、位置、最大动量、摩擦与电机电流/力矩等值最终写入官方 reactionWheelStateEffector 配置；IMU 与星敏安装、噪声、偏置及数据连接逐项验证。明确独立 source-native 部件与统一 Basilisk 内核的区别。
2. **EPS/太阳板**：统一 power node 的面板法线、跟踪、遮挡、功率输出及电池耗能证据；区别单机 builder 可选能力与整星实际调用路径。
3. **持续整星初始化**：现有只按 Orbit/ADCS/EPS/Payload/CommData 五组开放字段，后续补热控、推进及必要的固定安装参数，必须先证明值从 API 经过 native_values 进入实际原生图。不得为增加字段放松 Pydantic extra=forbid。
4. **其他单机及分系统**：对 MTB/CMG/磁强计、通信链路、载荷、热网络、推进开展相同的数据流及扰动门禁；项目简化模型仍保留，但 UI 显示真实保真度边界。
5. **发布**：参数审计 JSON 与真实 Basilisk 差异试验、Run Bundle 制品同步存档；没有动态证据的字段只能标为配置候选。

现有的 native_capability_audit.py 负责更细的 Basilisk API/字段映射分类；本报告负责面向用户操作的 Registry→表单→运行源字段差集，两者相互补充，不相互替代。
