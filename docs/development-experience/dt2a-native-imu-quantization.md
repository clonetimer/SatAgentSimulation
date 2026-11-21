# DT-2A｜Basilisk 2.11.0 IMU 原生量化（工程输入，不是硬件标定）

## 阶段边界

- **源基线**：main `feb6be73a79e122d001b3ba81abebd66261df90c`；先前 DT-1 精确提交 `a5147550` 的验收判据不变。
- **本 PR 只增加一项新机制**：官方 Basilisk `ImuSensor.setLSBs(accel, gyro)` 量化；默认两通道 LSB 都为 0，不触发新的原生调用。
- **显式参数**：`imu_gyro_lsb_rad_s`（0–0.1 rad/s）、`imu_accel_lsb_m_s2`（0–1 m/s²）。
- **原生链路**：正式 TaskSpec capability 参数 → 预启动表单/边界校验 → `UnifiedNativeRuntime` 官方 SWIG `ImuSensor` 的 `setLSBs(accel,gyro)` → 官方 `sensorOutMsg` → 原始 IMU recorder 与后续传感器融合。没有在 trace_rows 上后处理量化。
- **数学接口**：上游 [Basilisk 2.11.0 imuSensor.cpp](https://github.com/AVSLab/basilisk/blob/v2.11.0/src/simulation/sensors/imuSensor/imuSensor.cpp) 调用 `setLSBs(LSBa, LSBo)`，分别设置加速度/角速率量化；[discretize.cpp](https://github.com/AVSLab/basilisk/blob/v2.11.0/src/architecture/utilities/discretize.cpp) 默认 `TO_ZERO`、`carryError=false`。输出应落在对应 LSB 栅格；并不声称是特定真实 IMU 的分辨率。
- **独立性**：量化与既有随机种子白噪声、恒定偏置、安装角、量程参数不混淆；DT-2 随机游走/噪声谱、反作用轮死区/时延将在后续分别实现和验收。

## 三态验收任务（不得把未执行测试写成 PASS）

| 检查 | 判据 | 当前证据状态 |
|---|---|---|
| 0 默认不变 | 未选择与显式 `LSB=0` 的 raw 陀螺/加速度输出逐点相同 | **INCONCLUSIVE**；待 PR 原生 CI |
| 原生 gyro / accel 输出量化 | 官方原始 recorder 输出每个非初始化采样满足 `max |raw/LSB - round(raw/LSB)| < 1e-8`；量化与未量化 A/B 最大差 `>1e-6` 且 `<LSB+1e-6` | **INCONCLUSIVE**；待 PR 原生 CI 实测值 |
| 混合白噪声/量化种子复现 | 相同 `simulation_seed=331` 下两次 raw 输出逐轴对应 `abs/rel <=1e-12` | **INCONCLUSIVE**；待 PR 原生 CI |
| 参数边界 | 负值、溢出、非数、布尔与非法字符串必须预启动或 native 入口拒绝；default 0 | **INCONCLUSIVE**；待 PR CI |
| 全仓库既有原生回归 | 继续执行原 `continuous-satellite-native` 全链路与既有白噪声测试，不关闭任何物理门禁 | **INCONCLUSIVE**；待 PR CI |

**实际证据文件**：`tests/test_native_imu_quantization.py`；原生 CI 显式步骤 `DT-2A official native IMU quantization raw A/B and fail-closed inputs`，其日志会输出两行 `DT2_NATIVE_IMU_LSB_MEASURED` 和一行 `DT2_NATIVE_IMU_LSB_SEEDED_REPLAY`。必须看到该 PR **实际 SHA** 的运行数值后，才能把以上 INCONCLUSIVE 改为 PASS。

## 不能声称的结论

- 不是将 CAD/惯量/mesh 反推得到的传感器安装与量化值；所有 LSB 都是人工显式测试输入。
- 不是实物 IMU 噪声谱/Allan variance、长期零偏稳定性、带宽/群延迟或真实 ADC 数据的标定证据。
- 没有实现执行机构死区/时延、惯性传感器随机游走或不同采样速率抗混叠。
- 没有将模拟统计与真实飞行遥测混为一谈。DT-3 V&V 证据等级尚未提升。
- 浏览器端 `continuous-console-browser.yml` 在旧 `a5147550` 提交上的独立专项仍无运行记录，保持独立 **INCONCLUSIVE**，不在此 DT-2 PR 声称补验通过。

## 开发经验

1. **先查官方 2.11.0 SWIG 入口，避免自造 Python 噪声**：量化直接使用 upstream `setLSBs(accel,gyro)`，输入顺序不能倒置。
2. **输出必须取原始消息，不取修饰后曲线**：从 `adcs.sensor.raw_*` 检查 LSB 栅格，依旧保留实际物理闭环与测量融合。
3. **默认/边界同时验证**：新参数全部 opt-in，0 不调用原生 setters；非法值 fail closed，不悄悄截断到阈值。
4. **先完成完整 PR 验收再进入下一片**：不能以源码存在、静态检查或 PR 数量代替运行测量。
