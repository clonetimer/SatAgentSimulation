# P9-N4：单机工作台产品闭环

日期：2026-11-18
状态：实施中；以 P9-N1 反作用轮为首个端到端验收对象。

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

## 当前已知问题

- 既有 P9-N1 PR #168 的首个浏览器 head 有两处失败：一个测试将 UI 的 async 提交强行改为 sync，但前端仍按 async execution_state 轮询；另一个在向导视图下尝试点击隐藏的侧边栏历史入口。物理专项本身通过。此分支改为使用 embedded worker 保持 UI async 生命周期，并通过向导中可见的“其他专业工具 → 运行中心”进入历史；最终是否通过以当前 PR head 的实际 Chromium 结果为准。
- P9-N1 PR #166 的 backend provenance UI 变更与 N4-A 结果摘要重叠；最终只保留一套来源展示，不重复实现。

## 决策边界

- 不把所有 component capability 一次性暴露为已验证的原生单机。
- 不将项目参考模型结果标记为 Basilisk。
- 不以 CI 数量代替实际物理响应、浏览器交互和 sealed evidence。
- 不把 P9-N4 的有限时长单机闭环扩展成单机实时连续仿真。


## 已有原生能力与产品入口准入（2026-11-18）

依据当前 main 的 P9-N capability inventory，N4-C 不需要重新证明所有物理模型，但必须补齐每个设备的工作台模板、参数投影和结果曲线/指标合同后才能开放入口。

| 设备 | 官方 native TaskSpec / sealed 证据 | N4 产品入口决策 |
|---|---|---|
| 反作用轮 | 已通过；含官方 ReactionWheelStateEffector recorder | N4-A 首批开放；本 PR 验收 |
| IMU | 已通过；官方 ImuSensor 独立图 | N4-C 候选；先补专用模板与 recorder 曲线/单位配置 |
| 星敏感器 | 已通过；官方 StarTracker quaternion recorder | N4-C 候选；需把四元数输出与有效性语义明确展示 |
| 磁强计 | 已通过；官方 Magnetometer tam_S recorder | N4-C 候选；需补单位/坐标系说明和模板 |
| 电池 | 已通过；SimpleBattery + SimplePowerSink | N4-C 候选；需区分输入净功率配置与原生储能测量 |
| 太阳翼 | 已通过；SimpleSolarPanel recorder | N4-C 候选；需说明固定板/静态 Sun-Eclipse 边界 |
| 推力器 | 已通过；ThrusterDynamicEffector + FuelTank fixture | N4-C 候选；需展示点火配置与 recorder 积分输出的区别 |
| 贮箱 | 已通过；FuelTank 原生质量 recorder | N4-C 候选；原生模式不得显示参考压力字段 |
| 热节点 | 已通过；SensorThermal 原生温度 recorder | N4-C 候选；展示温度单位与固定边界假设 |
| 太阳敏感器 CSS | 仅官方标量光强 probe，未接入现有三轴方向 capability contract | 暂不开放 native 单机入口 |
| 加热器、散热器及其他项目模型 | 当前为项目 reference/source-native | 可在后续独立分组标明来源，不得混入 Basilisk-native 白名单 |

EPS UnifiedNativeRuntime 是分系统集成能力，不作为单个设备入口；不得把多个独立 Run 的后处理结果当作同一 SimBase 物理闭环。

- 共享工作台浏览器 workflow 不安装 Basilisk；原生真实 Run 浏览器旅程在缺少 Basilisk 时显式 skip，并由专用 native-RW workflow 安装、校验官方 2.11.0 后无跳过执行同一旅程。通用 UI suite 不应隐式承担原生运行时依赖。
