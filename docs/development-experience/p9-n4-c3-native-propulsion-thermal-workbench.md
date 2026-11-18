# P9-N4-C3 推进与热控原生单机工作台开发经验

日期：2026-11-18

## 本轮目标

在 N4-C1（反作用轮、IMU、星敏、磁强计）和 N4-C2（蓄电池、太阳电池阵）之后，将已有官方 Basilisk 物理与 sealed Run Bundle 证据的推力器、燃料贮箱、热节点接入同一“单机独立仿真”向导。复用既有 capability、adapter、Scenario Template、TaskSpec、Run Bundle、预检和结果历史链路。

## 设备模板与物理边界

### 推力器

- 模板：`component_thruster_basilisk_native`
- 官方模型：`ThrusterDynamicEffector` + `FuelTank` + `Spacecraft` 自由载体夹具。
- 配置：1 N、Isp 280 s、native step 0.005 s、单个连续 0.5 s 标称点火脉冲。
- 输出：推力 recorder、点火时间、累积冲量、累积推进剂消耗；冲量由 dense recorder thrust 积分，推进剂由 FuelTank 质量差得到。
- 边界：10 kg 自由载体、1 kg 初始推进剂、2 kg 贮箱容量、方向 +X、零杆臂；不是已安装整星推进系统，不包含轨道/姿态耦合。native faults/degradation 不开放。

### 燃料贮箱

- 模板：`component_fuel_tank_basilisk_native`
- 官方模型：`Basilisk.simulation.fuelTank.FuelTank`
- 配置：3 kg 容量、2 kg 初始质量、0.001 kg/s 恒定流量、10 kg 自由载体。
- 输出：propellant mass、mass rate、capacity recorder；final mass / consumed mass 从首末记录质量差得到。
- 边界：不提供压力或热力学状态；不能把 project_reference 压力代理字段放入 native 输出合同；不与推力器 Run 拼接成同一闭环。

### 热节点

- 模板：`component_thermal_node_basilisk_native`
- 官方模型：`Basilisk.simulation.sensorThermal.SensorThermal`
- 配置：固定辐射板、恒定 10 W 电功率、明确面积/质量/比热/吸收率/发射率、固定太阳方向和完全日食 fixture。
- 输出：温度 K/°C 与输入功率，温度 extrema / delta QoI 来自原生温度 recorder。
- 边界：固定太阳/航天器状态 fixture；不包含轨道传播、环境导热网络或地球红外/反照闭环。

## 实施改动

- 增加推力器原生模板；扩充燃料贮箱与热节点现有模板的输出白名单、推荐状态和产品边界说明。
- 将三类设备加入统一 native 设备 allowlist。
- 推力器原生结果 summary 补齐顶层 `physics_backend=basilisk_native`，并保留 metadata provenance；保证结果卡片和证据摘要一致。
- 增加模板/schema/API合同、原生 Run Bundle provenance 断言、Chromium 真实 Run/曲线/历史回放旅程。
- 官方 Basilisk CI 增加 thruster/fuel tank/thermal node 原生物理测试和 JUnit 证据归档。

## 工程经验

1. **不能用组件独立 Run 冒充分系统闭环。** 推力器和燃料贮箱分别通过独立原生夹具运行，不代表推力器—贮箱同一 SimBase 闭环；必须明确标注 fixture。
2. **native 输出白名单是安全合同。** FuelTank native 不支持压力输出；如果沿用 project_reference 的默认压力字段，用户可能误以为原生 FuelTank 已测得热力学压力。模板必须只请求实际 recorder 支持的质量、质量变化率和容量字段。
3. **点火脉冲的时间网格有物理意义。** Thruster native_step_s、simulation.step_s 必须在纳秒舍入后匹配；不能为了简化表单而隐去 native 时间步或将 sample_s 当成动力学积分步长。
4. **推力和冲量不能混为一谈。** effective_thrust_n 是 THROutputMsg recorder 值；total_impulse_ns 是 dense thrust recorder 积分。稀疏输出采样可能看不到短脉冲，但原生积分仍保留真实冲量。
5. **燃料消耗来自原生质量记录。** 不能用推力/比冲的解析值替代 FuelTank recorder；应校验冲量—推进剂质量守恒及数值舍入边界。
6. **热节点温度单位必须并列明确。** Basilisk SensorThermal recorder 输出 Celsius，K 是加 273.15 的派生字段；结果曲线必须同时显示单位和来源。
7. **backend 来源要同时出现在 summary 和 metadata。** 前端结果摘要读取顶层 backend，工程追溯读取 adapter metadata；两者缺一都会造成错误的“未声明”或来源丢失。

## 验收状态

PR 同一 head 必须通过：
- 官方 Basilisk 2.11.0 推力器、FuelTank、SensorThermal 物理/Run Bundle tests。
- 三个模板的 schema、产品可见性、TaskSpec validation 和 API instantiate。
- Chromium 真实 Run，核验 SUCCEEDED、PASS、sealed、integrity、native provenance、telemetry/plot manifest 与同一 Run 历史回放。
- N4-C1/C2 六设备和既有 P9-C 至 P9-M 回归。

CI 通过前不宣称 N4-C3 已完成。CSS、太阳敏感器三轴测量、推力器安装几何和 propulsion subsystem 闭环均不属于本轮范围。
