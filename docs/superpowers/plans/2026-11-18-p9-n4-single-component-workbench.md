# P9-N4：单机工作台产品闭环

日期：2026-11-19
状态：N4-A/B/C1/C2/C3 已合入 main；N4-D 正在补齐完整 Run Bundle 导出和九类设备端到端验收。

## 目标

让用户不必编辑 TaskSpec 或理解内部 adapter，即可在统一工作台完成：

单机选择 → 注册模板载入 → 参数配置 → 预检/校验 → 有限时长执行 → 曲线与指标 → 历史 Run 重开 → 数据/Run Bundle 导出。

只复用现有 Capability Registry、Scenario Template、Schema 表单、Canonical TaskSpec、Validator、Execution Planner、registered adapter、Run Bundle 和历史/导出 API。不建立第二套单机任务系统。

## 范围与原则

1. **能力白名单由证据驱动。** 首批入口只显示已通过官方 Basilisk 独立物理、正式 TaskSpec、sealed Run Bundle 和浏览器执行验收的单机。可执行 capability 不等于 Basilisk-native。
2. **模板来自服务端。** 选择设备后通过注册模板 API 获取配置，再无损投影到现有 Schema 表单；前端不复制物理默认值。
3. **参数可审计。** 显示参数中文名、单位、物理范围、默认值、参数来源和实际 backend。切换 backend 后清理不适用参数/输出并要求重新校验。
4. **预检失败关闭。** 未支持的故障、退化、事件、几何或耦合请求在构建原生图之前拒绝；不静默退回 project_reference。
5. **结果忠于 recorder。** 曲线、指标、来源和单位只呈现所选后端真实提供的字段。命令/配置、原生测量和派生统计分别标识。
6. **历史可复现。** 从历史 Run 打开时展示同一 TaskSpec、backend provenance、sealed/integrity 状态和相同曲线；不重建或替换原始结果。
7. **只做有限时长运行。** 单机 continuous/interactive 不在本阶段开放；不得借用整星实时会话接口。

## 实施批次

### N4-A：首个真实产品旅程——反作用轮

- 快速仿真入口提供“单机独立仿真”目标。
- 首批设备只列反作用轮，自动加载注册 Basilisk-native 单轮模板。
- 参数面板展示 native backend、轮数/惯量/力矩/速度等参数；原生仅 nominal 时禁用故障、退化和约束入口。
- 用户点击运行后真实执行官方 Basilisk，结果展示轮速和实际施加电机力矩曲线、关键速度指标及 native provenance。
- 从历史 Run Center 重开同一 sealed Run，验证来源和曲线不丢失。

### N4-B：预检与结果合同收敛

- 校验失败展示具体参数/能力边界，不仅给通用失败状态。
- 验证结果曲线只来自当前 backend 输出合同；无数据时明确空态。
- 检查 canonical/legacy TaskSpec、运行状态、validation、seal、integrity 和导出身份一致。
- 浏览器测试必须验证真实按钮旅程与真实 API/Run，不用 DOM 强制点击、伪造响应或仅验证模板被选中。

### N4-C：按证据扩展单机目录

#### N4-C1：传感器首批产品化（已合入 main，PR #179）

- 在现有 N4 工作台中开放 IMU、星敏感器、磁强计三个已通过官方 Basilisk 独立物理与 sealed Run Bundle 验收的设备。
- 每个设备使用独立注册的 `basilisk_native` Scenario Template；前端不复制默认物理参数。
- 结果只显示各自官方 recorder 字段和已有 QOI；单位从 Capability 输出合同读取。
- 单机向导对 native sensor/RW 统一只开放 nominal、有限时长离线运行，隐藏不支持的故障/退化事件编辑入口。
- 浏览器旅程必须实际执行三个设备并验证同一 Run ID、backend provenance、sealed/integrity、遥测字段和图表空/非空状态。

边界：
- IMU：显式角速度与非保守比力真值输入；identity mounting、零杆臂、无随机噪声，不宣称导航解算。
- 星敏感器：四元数转 MRP；valid 只表示 recorder 样本存在且四元数归一化，不宣称星表识别、光学成像或 FOV。
- 磁强计：显式均匀磁场真值；identity mounting/姿态、统一三轴 scale，不宣称地球磁场环境模型。
- 任何 native fault/degradation、安装姿态变化、实时单机和跨设备耦合仍保持未开放。

- 逐个评估 N2 传感器和 N3 EPS、推进、贮箱、热节点是否具备完整的用户模板与结果输出合同。
- 仅在独立物理、TaskSpec sealed Run、参数字典、浏览器结果和历史回放证据齐全后加入入口。
- 对暂不支持的 CSS 三轴方向、native fault/degradation、安装几何、跨设备闭环继续显示为未开放，不用邻近模型补位。

## 首阶段验收标准

- [ ] 入口只显示经过独立原生验收的设备。
- [ ] 从用户界面选择设备和注册模板，无手工 TaskSpec。
- [ ] 参数单位、范围、默认值、来源、backend 可见。
- [ ] 预检拒绝非法/不支持输入，且不进入原生图构建。
- [ ] UI 发起真实 finite Run，状态 SUCCEEDED、validation PASS、sealed、integrity PASS。
- [ ] 结果曲线与关键指标来自原生 recorder，且单位正确。
- [ ] 结果明确区分配置、原生测量和派生统计。
- [ ] 历史 Run 重开后 provenance 与曲线保持一致。
- [ ] 数据/Run Bundle 导出可复核，导出脚本保持同一 TaskSpec/backend 身份。
- [ ] Chromium 真实旅程与官方 Basilisk 2.11.0 物理门禁在同一最终 head 通过。

## 历史 PR 清理

- 旧 PR #168 的 guided native workbench 方案已由 N4-A（PR #178）及 N4-C1/C2/C3 的合入实现替代；该分支已关闭，避免重复入口和分叉实现。
- 旧 PR #166 的 provenance UI 与 N4-A 结果摘要重叠；main 已保留统一的 backend/native provenance 展示，该 PR 已关闭，不再维护第二套实现。

## 决策边界

- 不把所有 component capability 一次性暴露为已验证的原生单机。
- 不将项目参考模型结果标记为 Basilisk。
- 不以 CI 数量代替实际物理响应、浏览器交互和 sealed evidence。
- 不把 P9-N4 的有限时长单机闭环扩展成单机实时连续仿真。


## 已有原生能力与产品入口准入（2026-11-18）

依据当前 main 的 P9-N capability inventory，N4-C 不需要重新证明所有物理模型，但必须补齐每个设备的工作台模板、参数投影和结果曲线/指标合同后才能开放入口。

| 设备 | 官方 native TaskSpec / sealed 证据 | N4 产品入口决策 |
|---|---|---|
| 反作用轮 | 已通过；含官方 ReactionWheelStateEffector recorder | N4-A 已合入 main（PR #178） |
| IMU | 已通过；官方 ImuSensor 独立图 | N4-C1 已合入 main（PR #179）；官方 Basilisk 物理/Run Bundle 与 Chromium 真实 Run/历史回放 PASS |
| 星敏感器 | 已通过；官方 StarTracker quaternion recorder | N4-C1 已合入 main（PR #179）；官方 Basilisk 物理/Run Bundle 与 Chromium 真实 Run/历史回放 PASS |
| 磁强计 | 已通过；官方 Magnetometer tam_S recorder | N4-C1 已合入 main（PR #179）；官方 Basilisk 物理/Run Bundle 与 Chromium 真实 Run/历史回放 PASS |
| 电池 | 已通过；SimpleBattery + SimplePowerSink | N4-C2 已合入 main（PR #180）；官方 Basilisk 物理/Run Bundle 与 Chromium 真实 Run/历史回放 PASS |
| 太阳翼 | 已通过；SimpleSolarPanel recorder | N4-C2 已合入 main（PR #180）；官方 Basilisk 物理/Run Bundle 与 Chromium 真实 Run/历史回放 PASS |
| 推力器 | 已通过；ThrusterDynamicEffector + FuelTank fixture | N4-C3 已合入 main（PR #181，merge 7f9cf69）；官方物理与 Chromium 真实 Run/历史回放 PASS |
| 贮箱 | 已通过；FuelTank 原生质量 recorder | N4-C3 已合入 main（PR #181，merge 7f9cf69）；官方物理与 Chromium 真实 Run/历史回放 PASS |
| 热节点 | 已通过；SensorThermal 原生温度 recorder | N4-C3 已合入 main（PR #181，merge 7f9cf69）；官方物理与 Chromium 真实 Run/历史回放 PASS |
| 太阳敏感器 CSS | 仅官方标量光强 probe，未接入现有三轴方向 capability contract | 暂不开放 native 单机入口 |
| 加热器、散热器及其他项目模型 | 当前为项目 reference/source-native | 可在后续独立分组标明来源，不得混入 Basilisk-native 白名单 |

EPS UnifiedNativeRuntime 是分系统集成能力，不作为单个设备入口；不得把多个独立 Run 的后处理结果当作同一 SimBase 物理闭环。

- 共享工作台浏览器 workflow 不安装 Basilisk；原生真实 Run 浏览器旅程在缺少 Basilisk 时显式 skip，并由专用 native-RW workflow 安装、校验官方 2.11.0 后无跳过执行同一旅程。通用 UI suite 不应隐式承担原生运行时依赖。


## N4-C 第一批传感器入口实施（2026-11-18）

- 已在分支 `feat/p9-n4-c-native-sensors-20261118` 新增 IMU、星敏感器、磁强计三个官方 Basilisk native Scenario Template，复用现有 capability 与 adapter，不新增物理实现。
- 向导白名单扩展为反作用轮、IMU、星敏感器、磁强计四项；选择后调用服务端注册模板 API，再投影到现有 Schema 参数表单。
- 原生传感器均只开放 nominal finite/offline；向导隐藏故障/退化事件编辑区，adapter 仍 fail closed。
- 已新增模板/API 合同测试、三类传感器官方物理与 sealed Run Bundle 专项执行步骤、Chromium 真实 Run 旅程；PR #179 已合入 main。
- 传感器声明边界：IMU 为 prescribed truth、identity mount、零杆臂、无随机噪声特性；星敏 valid 不代表星表/光学/FOV；磁强计为均匀磁场 fixture、identity mounting、统一三轴 scale，不宣称地球磁场环境。
- 开发经验归档：`docs/development-experience/p9-n4-c-native-sensor-workbench.md`。


## N4-C2：EPS 原生单机产品入口（2026-11-18）

- 基于 N4-C1 已合入的统一单机工作台，新增蓄电池与太阳电池阵两个 native 模板；不新建 adapter 或执行链。
- 蓄电池使用 SimpleBattery + SimplePowerSink：恒定净功率、unity charge/discharge efficiency、nominal only；结果曲线显示 SOC、储能、净功率和施加功率。
- 太阳电池阵使用 SimpleSolarPanel：一块完全展开固定板、静态 Sun/Eclipse fixture、nominal only；结果曲线显示 panel/array power、incidence cosine 和 shadow factor。
- 两个 adapter 补充顶层 `summary.physics_backend=basilisk_native`，与 adapter metadata provenance 保持一致。
- 向导 native 白名单扩展为反作用轮、IMU、星敏、磁强计、蓄电池、太阳电池阵六项；仍禁用不受原生 adapter 支持的故障/退化编辑。
- 新增模板/API合同、官方 Basilisk native EPS Run Bundle 回归和六设备 Chromium 真实 Run/历史回放旅程；PR #180 已合入 main。
- 开发经验：`docs/development-experience/p9-n4-c2-native-eps-workbench.md`。


## N4-C3：推进与热控单机入口（2026-11-18）

- 新增推力器 native Scenario Template，使用官方 ThrusterDynamicEffector + FuelTank + Spacecraft 自由载体夹具；0.005 s 原生积分步、0.1 s 输出采样、单个连续 0.5 s 点火脉冲。
- 扩充 FuelTank 模板输出白名单，仅展示原生质量、质量变化率、容量及由记录质量差计算的推进剂消耗；不请求 native 不支持的压力字段。
- 扩充 SensorThermal 模板，输出温度 K/°C 和输入功率，明确固定辐射板/完全日食 fixture，不宣称轨道传播或地球红外/反照环境闭环。
- 工作台 native 白名单从六项扩展为九项；三个新增设备仍只开放 nominal finite/offline。
- 推力器 native 结果 summary 补齐顶层 backend 来源字段，与 adapter metadata 一致。
- 新增三类模板/API合同、真实 Chromium Run/历史回放旅程和官方 Basilisk 原生物理 JUnit 归档；PR #181 已合入 main。
- 开发经验：`docs/development-experience/p9-n4-c3-native-propulsion-thermal-workbench.md`。


## N4-C3 最终验收与合并（2026-11-19）

- PR #181 最终 head：e535341fa062bb222b65660b27493051e6fcaba3；Squash merge：7f9cf69d62415d710a64585a81237c37f11df001。
- 该 PR head 的 20 个 workflow runs 全部结束：19 success、1 个按条件跳过、0 failure；GitHub 报告与 main 同步且可合并。
- 官方 Basilisk 2.11.0 专项 #44323239995 PASS：RW 18、TaskSpec sealed Run 1、传感器 26、EPS 24、推力/贮箱/热节点 218、模板/API/Schema 14、native preflight 7；Chromium 4 passed / 4 deselected。
- 真实浏览器旅程覆盖反作用轮、IMU、星敏、磁强计、蓄电池、太阳电池阵、推力器、燃料贮箱、热节点；验证 SUCCEEDED、PASS、sealed/integrity、native provenance、recorder plot manifest 与同一 Run 历史回放。
- 证据 artifact native-component-physical-evidence：ID 14435583858，12,805 bytes，SHA-256 b2de89435ea80883d8439cfb32f70d5fe4266fa4056ffdbbb13429f45994a01c。

## N4-D：完整 Run Bundle 导出（2026-11-19）

当前候选分支 feat/p9-n4-d-run-bundle-export-20261119 正在补齐最后一项用户交付：

- 新增 GET /runs/{run_id}/bundle/download，仅允许已封存且 verify_run_bundle 完整性通过的 Run 导出。
- ZIP 保留原始 bundle_manifest.json、SEALED.json 和 manifest 中的全部文件；导出期间逐文件重新计算 SHA-256，任何文件变化、缺失、路径异常或身份不匹配均拒绝导出。
- 不打包运行锁，不遍历/导出 manifest 之外的临时文件；Run ID 限制为单个可移植路径段。
- 工作台仅在 sealed=true 且 integrity.ok=true 时显示“导出 Run Bundle”。原有“导出数据集”和“诊断包”语义保持不变。
- API 专项验证完整 ZIP、manifest/seal 绑定、所有文件哈希、未封存拒绝和篡改拒绝；Chromium 真实旅程逐个下载九类单机 Run 并校验 ZIP 内容。
- 本阶段仍以同一 PR head 的 CI 和浏览器下载证据为准；通过后再关闭 N4-D，进入 P9-N 分系统独立仿真路线评估。
