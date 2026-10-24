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

## 后续 P0 闭环顺序

1. **真实原生 ADCS**：确认 RW 安装轴、位置、最大动量、摩擦与电机电流/力矩等值最终写入官方 reactionWheelStateEffector 配置；IMU 与星敏安装、噪声、偏置及数据连接逐项验证。明确独立 source-native 部件与统一 Basilisk 内核的区别。
2. **EPS/太阳板**：统一 power node 的面板法线、跟踪、遮挡、功率输出及电池耗能证据；区别单机 builder 可选能力与整星实际调用路径。
3. **持续整星初始化**：现有只按 Orbit/ADCS/EPS/Payload/CommData 五组开放字段，后续补热控、推进及必要的固定安装参数，必须先证明值从 API 经过 native_values 进入实际原生图。不得为增加字段放松 Pydantic extra=forbid。
4. **其他单机及分系统**：对 MTB/CMG/磁强计、通信链路、载荷、热网络、推进开展相同的数据流及扰动门禁；项目简化模型仍保留，但 UI 显示真实保真度边界。
5. **发布**：参数审计 JSON 与真实 Basilisk 差异试验、Run Bundle 制品同步存档；没有动态证据的字段只能标为配置候选。

现有的 native_capability_audit.py 负责更细的 Basilisk API/字段映射分类；本报告负责面向用户操作的 Registry→表单→运行源字段差集，两者相互补充，不相互替代。
