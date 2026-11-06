# R84-R2-E0：R84-R1 原始 IMU 多特征回溯筛查

> 日期：2026-11-06  
> 状态：**EXPLORATORY_RETROSPECTIVE_ONLY**  
> 本轮未启动新 Basilisk 物理仿真；不构成独立确认，不训练诊断模型。

## 1. 目的与边界

R84-R1 的单一 delta_gyro_rms_3d 统计量没有形成稳定的健康—故障区分。本轮先利用已归档的 R84-R1 原始 IMU 观测，检查一组固定、可解释的时域特征是否值得投入下一轮新的原生仿真。

本轮**明确属于回溯探索**：特征筛查复用了 R84-R1 的 3,159 个唯一运行，因此不能把本结果当作独立验证或确认性结论。任何有希望的特征组合，都必须在新的独立 seed 原生运行中重新验证。

## 2. 来源完整性

- R84-R1 原生执行：[Actions #42210589499](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42210589499)
- 原始证据 artifact：[13498019393](https://github.com/clonetimer/SatAgentSimulation/actions/runs/42210589499/artifacts/13498019393)
- Artifact SHA-256：4bc1f9d0f788dce5e8d33aace1fe8860d739158793bcf6fae28352c785742a8e
- 原始 R83m ZIP SHA-256：db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796
- R84-R0 artifact SHA-256：5d1439272f651519fec011d9728165f3afc46b1b3d14d305357ae271b26752af

审计程序重新核验 R84-R1 artifact ZIP、manifest 中列出的全部成员文件哈希、嵌入的 R83m 原始 ZIP 与 R84-R0 artifact 哈希；并重新从 31 点原始 IMU 行计算 delta_rms_3d，与 R84-R1 已归档特征逐条核对。

## 3. 固定的八维原始 IMU 特征

输入只允许官方 Basilisk ImuSensor.sensorOutMsg.AngVelPlatform 的三个原始陀螺仪轴：

- imu.raw_gyro_rad_s_x
- imu.raw_gyro_rad_s_y
- imu.raw_gyro_rad_s_z

固定时间窗，两端包含：

- 故障前窗口：0.4–1.6 s，共 7 点；
- 故障后窗口：2.6–3.6 s，共 6 点。

特征不使用 hub 真值、故障标签、轮索引、FSW 命令或 QA_ONLY 数据：

| 特征 | 定义 |
|---|---|
| delta_mean_x/y/z | 后窗各轴均值减前窗各轴均值 |
| delta_std_x/y/z | 后窗各轴总体标准差减前窗对应标准差 |
| delta_rms_3d | 后窗三轴向量 RMS 减前窗三轴向量 RMS |
| delta_derivative_rms_3d | 后窗三轴一阶差分 RMS 减前窗一阶差分 RMS；差分除以固定 0.2 s 采样间隔 |

## 4. 多特征健康偏离分数

每个物理上下文（轮方向 × 初始姿态 × 参考激励）内使用 20 个健康校准运行：

c_j = median(x_1j, ..., x_20j)

s_j = max(1.4826 × MAD(x_j), 10^-8)

S = sqrt(mean(((feature - center) / scale)^2)) over 8 fixed features

校准阈值使用 20 个健康校准样本的 leave-one-out 分数，取 nearest-rank 90th percentile；应用于另一组独立 seed 的健康误报控制、loss50 与 loss75。该分数是固定的无监督健康偏离统计探针，不是训练出的分类器。

## 5. 结果

主噪声水平 sigma=0.0001 rad/s，共 27 个物理上下文，每个 cohort 540 次运行。

| 统计探针 | 独立健康控制误报 | loss50 超阈值 | loss75 超阈值 |
|---|---:|---:|---:|
| R84-R1 原始单变量 delta-RMS | 93/540（17.22%） | 99/540（18.33%） | 84/540（15.56%） |
| R84-R2-E0 八维 robust score | 48/540（8.89%） | 49/540（9.07%） | 62/540（11.48%） |

八维分数相对独立健康控制的 context-level Cliff's delta：

| 故障严重度 | 平均值 | 中位数 |
|---|---:|---:|
| loss50 | -0.0309 | 0.0150 |
| loss75 | -0.0226 | -0.0250 |

各物理上下文中，Cliff's delta 方向并不稳定。独立健康控制、loss50、loss75 的超阈值率彼此接近，尚无足够证据说明多特征分数形成了可靠故障分离。

### 单项特征筛查

| 特征 | 健康误报 | loss50 超阈值 | loss75 超阈值 |
|---|---:|---:|---:|
| X 轴均值变化 | 12.04% | 13.89% | 13.33% |
| Y 轴均值变化 | 12.78% | 14.63% | 11.30% |
| Z 轴均值变化 | 12.78% | 10.56% | 15.74% |
| X 轴标准差变化 | 12.59% | 9.81% | 9.63% |
| Y 轴标准差变化 | 10.37% | 11.85% | 10.74% |
| Z 轴标准差变化 | 13.52% | 12.04% | 12.22% |
| 三轴 RMS 变化 | 12.96% | 12.41% | 11.11% |
| 三轴差分 RMS 变化 | 9.81% | 10.19% | 12.59% |

单项特征也没有显示稳定且明显高于健康误报的故障响应。

## 6. 研究决策

**不建议仅凭本轮结果再投入一轮大规模 IMU-only 原生运行，也不应把八维分数升级为诊断器。**

本轮支持的结论是：在当前 FSW、反作用轮、初始状态、激励和 IMU 噪声条件下，仅使用陀螺仪三轴时域统计量，故障与健康分布重叠明显。

下一阶段优先做**可用遥测通道审查**，再决定是否开展新的原生多传感器研究：

1. 核实 rwSpeedOutMsg / 轮速反馈在真实任务接口中是否作为可用遥测输出，而不把仿真状态自动等同于传感器测量。
2. 核实轮速测量的采样率、量化分辨率、噪声、偏置、丢包与时间同步；没有设备依据的参数只能做显式敏感性假设。
3. 审查执行机构状态、驱动电流/电压等信号的实际可用性及其 Basilisk 原生建模路径。
4. 只有完成接口和测量链审查后，才预注册新的独立 seed 多通道原生实验。

本轮不生成正式训练集，不访问受保护 VAL/TEST/FINAL，不报告分类准确率或正式误报资格，不作硬件/飞行安全或 Stable 结论。

## 7. 可复现命令

~~~bash
python -m pytest -q tests/test_r84r2_raw_imu_feature_screen.py

python scripts/audit_r84r2_raw_imu_feature_screen.py \
  --artifact ./r84r1-independent-noise-native-observability.zip \
  --report ./r84r2-e0-feature-screen.json
~~~

审计脚本拒绝任何非指定 SHA 的输入 artifact，且对原始 sensor 行执行严格字段白名单检查。
