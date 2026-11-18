# P9-N4-C1：官方 Basilisk 传感器单机入口经验

日期：2026-11-18

## 本轮改动

- 在现有 Scenario Template 注册表中增加 IMU、星敏感器、磁强计三个 Basilisk-native 模板。
- 单机向导复用 capability object ID → template ID 的白名单映射，选择设备后通过服务端模板实例化，再投影回原有 Schema 表单。
- native 单机向导隐藏故障/退化事件编辑区；不支持的事件不以“禁用后仍可提交”的方式暴露。
- 增加模板准入测试、API 发布测试和真实 Chromium 三类传感器有限时长 Run 旅程。

## 设备边界

| 设备 | 官方模型/输出 | 必须保留的限制 |
|---|---|---|
| IMU | `ImuSensor`；`AngVelPlatform`、`AccelPlatform` | 显式角速度/非保守比力真值；identity mounting、零杆臂、无随机噪声/导航解算 |
| 星敏感器 | `StarTracker`；四元数 recorder 转 MRP | valid 仅表示样本存在且四元数归一化；不表示星表、光学成像、FOV 或遮挡 |
| 磁强计 | `Magnetometer`；`tam_S` | 显式均匀磁场输入；identity mounting/姿态、统一 scale；不宣称地球磁场环境模型 |

三类 native 模型都只开放 nominal、有限时长离线运行。任何 native fault/degradation、非 identity mounting、未验证噪声模型、单机实时和跨设备闭环继续 fail closed。

## 工程经验

1. 已有官方 native probe 和 sealed Run Bundle 不代表产品入口已经完成；仍需要注册模板、参数投影、输出默认选择、UI 结果展示和历史 Run 合同。
2. 前端必须从服务端模板获取参数，不能复制 adapter 默认值；同一 Capability 的 project_reference 和 basilisk_native 通过显式 backend 字段区分。
3. 结果字段必须来自官方 recorder；StarTracker 的 quaternion→MRP 转换属于明示的表示变换，不能把 valid 误解成光学识别成功。
4. Magnetometer native backend 只有统一 scale；不能把 reference backend 的三轴 scale 参数悄悄压缩成一个数。
5. native-only 向导隐藏事件编辑区，同时保留后端 preflight 的 fail-closed 校验；前端隐藏不是安全边界。
6. 一次 Chromium 测试应对三个传感器分别发起真实异步 Run，检查 sealed/integrity、backend provenance、recorder 字段和图表，而不是只验证模板名称或 DOM。

## 验收状态

当前代码新增三个注册模板和浏览器旅程；最终准入以 PR 的官方 Basilisk 2.11.0 物理/Run Bundle 测试、真实 Chromium Run 及历史证据为准。在 CI 完成前不宣称 N4-C1 已验收。


## 首次 Chromium 失败复盘

第一次真实浏览器 Run 已经执行成功、Run Bundle 已封存，失败发生在验收断言：测试错误地期待 sensor 的 `summary.json` 顶层含有 `physics_backend`。实际合同将详细后端与模型来源保存在 `summary.adapter_metadata`，前端 `nativeProvenance` 也使用该证据字段。为使不同原生单机的结果摘要一致，三个传感器 native 分支现同时在 `summary.physics_backend` 暴露归一化后端标记，并保留 `adapter_metadata.physics_backend/native_provenance` 的详细来源证据。浏览器验收同时检查两层字段。

经验：后端类型是通用结果字段，适合放在 summary 顶层；模型来源、输入来源和 fidelity limits 则留在 adapter_metadata。不要把所有 metadata 无差别复制到 summary 顶层。
