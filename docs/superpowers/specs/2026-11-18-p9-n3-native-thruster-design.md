# P9-N3 原生推进器单机设计

## 目的与既有授权

延续已确认的 P9-N 单机独立仿真路线，在 EPS 首批 PR #171 合入后，完成推进器的 opt-in 官方 Basilisk 有限运行。用户已要求持续实现、记录经验、完成验收后合并；本次不重复请求设备级确认。

## 现有事实与实现选择

`component.thruster.v1` 的 `ThrusterAdapter` 当前逐输出周期调用项目 `compute_thruster_pulse`。`on_time_s` 是每周期命令，`on_time_profile_s` 缺少末项时沿用最后一项；不能直接把它替换成一次原生点火。现有 `build_propulsion_basilisk_sim` 可用一台官方 ThrusterDynamicEffector、Spacecraft 与 FuelTank 构成最小运行图。裸 thruster 没有动力学状态链接，不能作为独立积分器运行。

实现选择由开发侧作出：首批把符合原有周期命令含义的一个连续点火区间编译到既有运行图，保留零点火对照；不支持的多段脉冲明确拒绝。相比直接使用单次点火（改变语义）或新增任意遥控回放器（扩大本批范围），该方案同时保持接口含义和真实物理来源。

## 行为与边界

- 原 capability、adapter、默认 project_reference 行为和故障/退化路径保留；新增 physics_backend=basilisk_native。
- Native 只支持 nominal，所有 fault/degradation/constraint 事件明确拒绝。
- 沿用输入周期=simulation.sample_s。每周期请求必须在 [0,sample_s]。将 profile 按既有规则扩展到 n 个周期；空、非有限、超长或无效 profile 拒绝。允许全零，或连续非零区间，其中除最后一个非零周期外都必须为整周期。区间结束后不得再次点火。
- duration、sample、native_step_s 都必须为有限正值；经 Basilisk 纳秒舍入后 duration/sample 和 sample/native_step 均整除。非零点火持续时间也须与 native step 对齐；native_step_s 默认 0.01 秒。对超过 100000 个原生步长的有限运行请求明确拒绝。
- 一台推力器，固定 +X、零力臂；10 kg 自由载体、1 kg 初始推进剂、2 kg 容量是显式标识的试验夹具，不代表装机姿态或实际卫星。预检按点火时长加一个原生步长的质量流量预留余量，拒绝会耗尽夹具燃料的请求。
- 复用现有组件工厂和推进图，不调用整星运行图，不用 Python 代算物理响应。记录官方 THROutputMsg 与 FuelTankMsg；数值冲量从密集原生推力记录积分，消耗质量从原生贮箱质量差得到。
- 输出遵守现有五个 trace 字段和两个 summary QoI。on_time_s 是命令，effective_isp_s 是 nominal 配置参数；effective_thrust_n 在 native 路径表示该输出时刻记录的实际推力。元数据必须分别标明命令、配置、原生记录和派生统计。
- native physics step 与输出 sample 分离：用 dense recorder 计算累计冲量，再按真实记录时间抽取输出；不得因为低输出频率遗漏短脉冲。
- 预检和执行共享 capability、任务类型、目标与物理边界校验；缺依赖、缺 recorder、缺时间点或非有限物理记录均失败关闭。正点火还须满足贮箱质量的数值分辨率预算，执行后在每个密集时间点核对原生质量差与推力积分的守恒残差。

## 验收

真实 Basilisk 2.11.0：延迟点火与关断、零点火、推力幅值、比冲改变时质量流率响应、步长有界的冲量/燃料守恒、拒绝多段/重叠/非法网格/事件、正式 Run Bundle 的 SUCCEEDED/validation PASS/sealed/integrity，以及默认参考模型兼容性。只运行本批相关验证和适用 CI 门禁。
