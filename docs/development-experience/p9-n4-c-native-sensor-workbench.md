# P9-N4-C 原生传感器工作台扩展经验

日期：2026-11-18

## 本轮交付范围

在 P9-N4-A 已合入主干的统一“单机独立仿真”入口中，增加三类已有官方 Basilisk 物理与 sealed Run 证据的设备：

- IMU：官方 `Basilisk.simulation.imuSensor.ImuSensor`；输入显式角速度和非保守比力真值；输出三轴 gyro/accel recorder 测量。
- 星敏感器：官方 `Basilisk.simulation.starTracker.StarTracker`；输出 recorder 四元数换算的 MRP、有效标记与漂移量。有效标记不代表星表识别或光学视场。
- 磁强计：官方 `Basilisk.simulation.magnetometer.Magnetometer`；输入显式均匀磁场，输出 sensor-frame 三轴磁场和模长；统一三轴 scale、identity mounting。

实现方式是复用已有 capability、adapter、TaskSpec、Scenario Template、表单投影、预检和 Run Bundle，不建立第二套单机运行系统。新模板分别为：

- `component_imu_basilisk_native`
- `component_star_tracker_basilisk_native`
- `component_magnetometer_basilisk_native`

工作台设备白名单只包含已完成官方 native 物理及 sealed Run 证据的四项：反作用轮、IMU、星敏感器、磁强计。CSS 仍不开放，因为当前官方 probe 是标量光强，不满足现有三轴方向输出合同。

## 实施经验与踩坑

1. **产品目录不能等同于 capability 目录。** 仓库里已经有 IMU/星敏/磁强计的 native adapter 和 sealed Run 测试，但原生模板和统一工作台入口缺失。入口必须由明确的 native-template 白名单驱动，而不是将所有可执行 component 自动放出。
2. **不要虚构新 capability 或重复实现 adapter。** 这三类设备复用原 capability id 和已有 Basilisk adapter；只新增模板与产品入口映射。默认 backend 仍为 `project_reference`，模板显式指定 `basilisk_native`。
3. **每个设备的输出合同不同。** IMU 用 rad/s 与 m/s²；星敏用 MRP 并明确 valid 语义；磁强计用 T 且必须说明 sensor frame 和均匀场 fixture。不能将相同曲线字段、单位或“有效”解释套给不同设备。
4. **native nominal-only 必须从 UI 到后端双重约束。** 向导中隐藏事件编辑区，后端仍保留 fail-closed 预检。隐藏 UI 不是安全边界，预检也不能替代清晰的用户提示。
5. **浏览器测试应走真实工作流。** 对每个设备选择注册模板、校验、执行 embedded async Run，核对 SUCCEEDED、PASS、sealed、SHA-256 integrity、backend provenance 和实际 telemetry 字段；不能只断言按钮可见或模板名称存在。
6. **断言本地化文案时，先核对产品实际呈现。** 浏览器提示不应硬编码内部 Basilisk Python 类路径；技术来源应在结果 provenance 中验收，用户提示应使用设备名称和边界说明。
7. **工作台参数投影必须保留服务端模板来源。** 不在前端重复写物理默认值；由服务端模板 instantiate 后经 `projectTaskSpecToForm` 投影。

## 本轮待验收

CI 必须在同一 PR head 完成：

- 官方 Basilisk 2.11.0 传感器 probe、输入边界、adapter 路由及 sealed Run Bundle 测试。
- 三类传感器模板 schema、产品可见性、TaskSpec validation 与 API instantiate 测试。
- Chromium 真实用户旅程，三种设备各执行真实 Run 并检查 provenance、telemetry 与曲线。
- 反作用轮既有旅程不回归；整星/分系统和 P9-C 至 P9-M 既有合同不回归。

不把本轮结果扩大解释为传感器随机噪声标定、非 identity 安装、地球磁场模型、光学星表/FOV、故障/退化或单机实时能力。


## Chromium 首轮失败复盘：Run 摘要后端来源字段（2026-11-18）

首轮三传感器真实 Chromium 测试中，IMU Run 成功，但星敏感器 Run 的 sealed detail 摘要缺少顶层 `physics_backend`，导致 UI 来源字段只能显示“未声明”。原因是 IMU native 分支已有 `summary["physics_backend"]`，而 StarTracker / Magnetometer native 分支只在 `SimulationResult.metadata` 中记录 backend，未写入用户结果摘要。

修复：
- StarTracker、Magnetometer native 分支均将 `physics_backend=basilisk_native` 写入 summary。
- Run Bundle 合同测试对三种传感器统一断言 summary backend、adapter metadata backend、native provenance 均存在。
- Chromium 仍按真实 sealed Run detail 验证 backend 与 provenance，不降低断言、不用 metadata-only 结果掩盖产品摘要缺口。

经验：adapter metadata 是工程证据存储，summary 是用户结果合同；两者都要保留 backend。仅 metadata 正确，用户结果页仍可能误报“未声明”。


## 修复后专项验收结果（2026-11-18）

修复后的官方 Basilisk + Chromium 专项在 PR #179 当前代码头通过：

- 官方 Basilisk 2.11.0 反作用轮物理 A/B：18 passed。
- 三种传感器官方物理、adapter 和 sealed Run Bundle：26 passed。
- 三类 native 模板、Schema 表单和 API 发布：12 passed。
- N4-B native preflight/result 合同：7 passed。
- Chromium 用户旅程：4 passed、4 deselected；其中包括三类传感器真实 Run、sealed/integrity、backend provenance、recorder plot manifest 与 Run Center 同一 Run 历史回放。
- 原生物理与 JUnit 证据包：成功归档，artifact `native-component-physical-evidence`，8,552 bytes，SHA-256 `acedb415ed1656fa7fe6c66980898312b338fc1b768d59375e339a7e8d159ea9`。

专项工作流：`44310950498`，结论 SUCCESS。其余 PR 共享回归（Agent 输入一致性、热节点长测、整星 Basilisk 回归、P9-G 默认入口）仍在运行；因此专项通过不等于 PR 全部发布门禁完成。
