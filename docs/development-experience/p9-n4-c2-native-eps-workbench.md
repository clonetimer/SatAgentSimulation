# P9-N4-C2 原生 EPS 单机工作台开发经验

日期：2026-11-18

## 本轮目标

将已经具备官方 Basilisk 单机物理与 sealed Run Bundle 测试的电池和太阳翼，接入已经合入主干的统一“单机独立仿真”向导。复用既有 capability、adapter、Scenario Template、TaskSpec、参数表单、预检、Run Bundle、结果曲线和历史运行中心，不建立第二套执行系统。

## 新增产品模板

### 蓄电池

- 模板：`component_battery_basilisk_native`
- 官方模块：`Basilisk.simulation.simpleBattery.SimpleBattery`、`SimplePowerSink`
- 输入：容量、初始 SOC、恒定净功率。
- 当前原生约束：nominal only、恒定净功率、充放电效率均为 1.0、SOC 区间 [0,1]、完整采样间隔；不含电芯等效电路、热模型、故障和退化。
- 输出：SOC、储能 Wh、净功率 W、施加功率 W；派生能量吞吐和容量截断量必须注明其 recorder/积分来源，不能描述成已建模的分流电路或热耗散。

### 太阳电池阵

- 模板：`component_solar_panel_basilisk_native`
- 官方模块：`Basilisk.simulation.simpleSolarPanel.SimpleSolarPanel`
- 输入：额定功率、效率、单块完全展开的固定板、静态太阳方向、静态阴影系数。
- 当前原生约束：nominal only、单块完全展开固定板、静态 Sun/Eclipse、无跟踪、无故障/退化/约束、完整采样间隔；不包含轨道传播或地球光照环境。
- 输出：panel/array power、入射余弦、shadow factor、由 recorder 功率积分的发电能量。

## 代码改动

- 新增上述两个注册 Scenario Template，显式将 `physics_backend` 设置为 `basilisk_native`。
- 扩展单机工作台 native 白名单，保留反作用轮、IMU、星敏、磁强计，并加入蓄电池和太阳电池阵。
- 选择设备后仍调用公开 Scenario Template instantiate API，再投影到已有 schema-driven 参数表单。
- native 单机向导继续隐藏故障/退化事件编辑；后端 adapter 保持 fail-closed。
- 修正 BatteryAdapter 与 SolarPanelAdapter 的用户结果 summary：顶层 `summary.physics_backend` 必须显式为 `basilisk_native`，详细官方模块与来源保留在 `summary.adapter_metadata`。

## 重要工程经验

1. **元数据正确不代表结果卡片正确。** Run Bundle 的 adapter metadata 已记录 backend，但结果摘要没有顶层 backend 时，用户界面会显示“未声明”。物理来源必须同时出现在 adapter metadata 和 summary。
2. **复用已有原生测试，不复制物理实现。** Battery/SolarPanel 已有官方 SimpleBattery/SimplePowerSink/SimpleSolarPanel adapter 与 sealed Run tests，本阶段只补产品模板、目录入口和用户旅程。
3. **按原生模型约束选择默认参数。** 电池效率固定 1.0、净功率恒定；太阳翼单板全展开、静态方向和阴影。不可让模板默认值看似支持原生 adapter 实际拒绝的工况。
4. **曲线与指标应来自真实字段。** 电池 SOC/储能/功率，太阳翼阵列功率/入射余弦/阴影；能量是对 recorder 功率积分，不得把输入额定值当测量值。
5. **产品对象名称以 object catalog 为准。** 蓄电池和太阳电池阵在目录中的中文名称分别为“蓄电池”和“太阳电池阵”，测试和用户界面不自行发明别名。
6. **验收覆盖必须闭环。** 模板/schema/API、官方 Basilisk 物理与 sealed Run Bundle、真实 Chromium 运行、曲线清单、backend provenance、Run Center 同一 Run 回放都要覆盖；只检查模板存在不算开放完成。

## 当前验收状态

本分支新增：
- EPS 模板/schema/API合同测试。
- 原生 battery + solar adapter / sealed Run Bundle 的来源摘要断言。
- Chromium 用户旅程扩展为 IMU、StarTracker、Magnetometer、Battery、SolarPanel 五种传感器/EPS 设备。
- 官方 Basilisk 工作流执行 `tests/test_native_battery_component.py` 并归档 JUnit 证据。

最终以 PR 同一 head 的官方 Basilisk 2.11.0 + Chromium 工作流结果为准；CI 通过前不宣称 N4-C2 已完成。
