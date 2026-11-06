# R84 原生 IMU × 反作用轮故障可观测性：R0 小规模预注册

> 预注册日期：2026-11-06（JST）。
> **状态：PREREGISTERED_NOT_EXECUTED；本文件不代表已运行任何 R84 Basilisk 仿真。**

## 1. 背景与物理边界

[R83m 原始 54 案例审计](../reports/native_rw_cluster/r83m_data_eligibility_review_20261106.md) 验证了真实 Basilisk 反作用轮（RW）在 75% 力矩权限损失时：静默参考和 0.5° 激励下轮速效应为 0/9，0.9° 为 9/9。这是物理状态和 C++ 执行机构读回的结果，不代表陀螺仪在噪声下也一定能够检测故障。

R84-R0 只验证**真实原生测量链的可用性和故障可观测性**，不训练算法。保留 R83m 的整星动力学、FSW、轮系与实际 C++ 权限故障链，独立新增官方 Basilisk ImuSensor 作为旁路观察者。实验设计见 [冻结 JSON](../configs/research/r84_native_imu_observability_pilot.json)。

运行时固定使用原始 R83m 相同的官方 bsk 2.11.0 Linux Wheel，SHA-256 为 `d51fe1ffa1b0b03dc415034b210497b28a2504428d4d8e71b481542a08c08a0a`。R83m 原实验构建产物的发行元数据标记为 satfix1；当前 mainline 构建工具使用 satfix2。R84 采用当前工具生成 satfix2 metadata-only Wheel，但必须证明 880 项原生载荷逐字节不变，且再次核验原始 Wheel SHA。该变更只涉及发行元数据和 Pillow 依赖标记，不替换、不重新编译 Basilisk 物理模块。

## 2. 固定八个实验臂与一项重复性控制

| 维度 | 固定条件 |
|---|---|
| 轮系与初始状态 | RW0、initial0 |
| 姿态激励 | 0°、0.9° |
| 力矩权限 | 正常 0.020 N·m，故障后 0.005 N·m |
| 官方 IMU 陀螺白噪声标准差 | 0、0.0001 rad/s |
| 随机种子 | 331 |
| IMU 安装 | 零安装角、质心位置 |
| 采样及仿真时间 | 0.2 s、6.0 s、31 行 |
| 参考调度及故障时刻 | 1.8 s 激励、3.8 s 恢复、2.4 s 故障、2.6 s 首个受影响采样 |

共有 2 种激励 × 2 种故障条件 × 2 种 IMU 噪声条件，即 8 个唯一实验臂。另对 pulse09/loss75/0.0001 rad/s 噪声臂以相同种子 331 完整重复一次，作为确定性回放 QA；因此总计 9 次执行，但仍只有 8 个唯一条件，重复运行不作为独立样本。健康与故障对使用相同随机种子，仅作为受控物理配对，不能声称部署阶段存在已知的健康孪生数据。0° 条件下不可辨识的真实故障不得重标为正常。

## 3. 必须完成的官方 Basilisk 调用链

1. 将 ImuSensor 的 scStateInMsg 真正连接到 spacecraft.scStateOutMsg，同一 native Process/Task 接收状态。
2. 将官方 sensorOutMsg.AngVelPlatform 原生 recorder 记录成 imu.raw_gyro_rad_s_x/y/z，不允许在已保存的状态轨迹上事后添加 Python/NumPy 噪声。
3. 官方 PMatrixGyro、setAMatrixGyro(0)、RNGSeed 实现白噪声与随机种子；验证同种子复现以及噪声开启前后有真实观测差异。
4. 保留 R83m 使用 simpleNav 驱动的 FSW 闭环，新增 IMU 只能作为观察者；加入观察器前后，动力学、轮速、原生施加力矩和姿态状态应保持一致。
5. 继续使用真实 C++ ReactionWheelData[target].u_max 权限变更并对照 rwOutMsgs[target].u_current；两者仅用于离线 QA，不得作为诊断输入。
6. 沿用既定临时研究观测上界（请求力矩不超过 0.02 N·m、轮速不超过 900 rad/s、机体角速率不超过 0.005 rad/s）。这是研究观测包络，不是飞行安全资格。

主干已有[官方原生 IMU 噪声专项测试](../tests/test_native_imu_white_noise.py)，但它不是新 R84 实验的完成证据。

## 4. 观测特征与防泄漏

候选部署观测范围当前仅限官方原生 ImuSensor 的三个陀螺仪测量轴。RW 轮速、FSW 参考和轮请求属于待审核的命令/遥测信号，不得直接假定全部在任务现场可用。仿真 hub 真值、故障轮索引、故障注入时间、实际施加力矩、权限上限、所有 QA_ONLY/source/label 字段都必须与模型输入彻底隔离。

**本阶段不建立训练集、不读取受保护 VAL/TEST/FINAL、不产生模型诊断准确率结论。**

## 5. R0 验收和停止条件

R0-A：官方 IMU SWIG 实际接入、时间戳一致、种子回放、原生 noise-on/off A/B；R0-B：完成固定 8 臂 Basilisk 原生仿真并保留源代码/轮二进制 SHA 与原始消息；R0-C：独立复算可观测性，保留负例，记录可能的传感器噪声掩蔽。

官方 IMU 不可用、仅有后处理噪声、SWIG/C++ 读回失效、观测器改变原有 FSW 闭环、真值泄漏或超出固定实验边界，均应明确拒绝并保存失败证据。不得放宽阈值或从纯 Python 曲线替代原始物理数据。

## 6. 本 PR 的交付范围

本轮只冻结研究设计与相应静态契约测试，可执行的八臂原生科研源代码及真实实验收据需要另外独立实现并验证。静态自检命令：

    python -m pytest -q tests/test_r84_native_imu_prereg_contract.py

此项测试通过不等于 R84 物理实验成功，不影响现有 main 与 Stable 资格。
