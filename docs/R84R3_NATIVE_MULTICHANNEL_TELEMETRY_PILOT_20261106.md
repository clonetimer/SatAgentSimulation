# R84-R3A 原生多通道遥测接入试验预注册

> 日期：2026-11-06  
> 预注册状态：PREREGISTERED_NOT_EXECUTED（保留原冻结协议）。  
> 当前执行状态：PASS，详见 R84-R3A 执行报告。
> 本轮仅验证原生消息接入、来源与物理一致性，不训练模型、不生成正式数据集。

## 1. 为什么从 IMU-only 转向多通道

R84-R1 已完成 3,159 次独立 seed 原生仿真；R84-R2-E0 对相同数据的回溯筛查表明，单纯增加陀螺仪时域统计量仍无法有效区分健康与 RW 力矩权限损失。

仓库已有 ADCS RW 遥测特征契约，声明了轮速、FSW 命令力矩、实际力矩估计、指向误差和由命令力矩推导的电流。本试验逐项核对来源，不把这些字段混为同一种“传感器测量”。

## 2. 通道来源与证据角色

| 通道 | Basilisk 原生来源 | 本轮定位 | 模型输入候选 |
|---|---|---|---|
| IMU 三轴角速度 | ImuSensor.sensorOutMsg.AngVelPlatform | 官方传感器测量，含设定白噪声 | 是 |
| 三个 RW 轮速 | reactionWheelStateEffector.rwSpeedOutMsg.wheelSpeeds | 理想原生状态反馈上界，无编码器噪声/量化 | 暂定；不是硬件传感器资格 |
| 三个 RW 命令力矩 | FSW rwMotorTorqueOutMsg.motorTorque | 控制命令遥测，不等于实际交付力矩 | 是 |
| 指向误差 | attTrackingError.attGuidOutMsg.sigma_BR 转换 | FSW 运行估计 | 暂定 |
| 实际施加力矩 | rwOutMsgs[target].u_current | C++ 执行机构物理读回 | QA-only |
| 最大力矩权限 | ReactionWheelData[target].u_max | 故障注入真值 | QA-only |
| 电机电流 | 当前没有原生电流传感器模型 | I=tau/Kt 只是命令推导估计，不是实测电流 | 排除 |

特别说明：本轮轮速来自 Basilisk 原生状态反馈消息，但没有额外编码器噪声、量化、偏置、丢包或硬件标定。因此它只能作为理想遥测上界，不能宣称为真实飞行轮速传感器测量。命令力矩也不得冒充实际交付力矩。

## 3. 固定 12 个物理工况

| 维度 | 配置 |
|---|---|
| 目标轮/初始姿态 | RW0 / initial0 |
| FSW 参考激励 | 0°、0.9° |
| 执行机构权限 | healthy 0.020、loss50 0.010、loss75 0.005 N·m |
| IMU 白噪声 | 0、0.0001 rad/s |
| 唯一工况 | 2 × 3 × 2 = 12 |
| 确定性回放 | 1 次 |
| 总原生执行 | 13 次 |
| 仿真时间/采样 | 6 s / 0.2 s / 31 点 |
| FSW 参考设置/恢复 | 1.8 s / 3.8 s |
| 故障施加 | 2.4 s scheduler 暂停边界；2.6 s 首个故障后样本 |

所有工况保留原 R83m spacecraft、simpleNav、FSW、reactionWheelStateEffector、C++ u_max 权限修改和官方 IMU。新增的 tracking recorder 只读 attGuidOutMsg，不连接到控制反馈。

随机种子固定公式：

    50000000 + reference_index*100000 + capacity_index*10000 + noise_index*1000 + 331

12 个唯一工况 seed 全部不同；重复回放只重复 pulse09/loss75/noise-on 工况的 seed，不作为独立样本。

## 4. 数据与 QA 隔离

- 多通道遥测文件：只含 time、IMU 三轴、三轮轮速、三轮 FSW 命令力矩和 FSW 指向误差。
- IMU 子集文件：仅官方 IMU 原始测量。
- case manifest：保存工况、seed 和事件回执。
- physics QA：保存 C++ u_max、实际施加力矩、hub 真值、R83m/R84-R1 基准对照。

每个运行必须核对：
1. 13 个 recorder 的 31 点时间轴严格对齐；
2. 三轮轮速字段逐点等于 rwSpeedOutMsg；
3. 三轮命令力矩字段逐点等于 rwMotorTorqueOutMsg；
4. 指向误差来自 attGuidOutMsg.sigma_BR，而不是 hub 真值；
5. IMU 字段与独立 IMU recorder 完全一致；
6. 真实 C++ 权限读回和实际施加力矩满足对应容量；
7. healthy/loss75 对照 SHA 固定 R83m 原始实验，loss50 对照 R84-R1 的同上下文零噪声原生基准；
8. IMU noise on/off 只改变 IMU 原始测量，不改变轮速、FSW 命令或指向误差；
9. 固定 seed 回放完全一致。

## 5. 本轮明确不做的事

- 不把理想轮速反馈称为已验证编码器测量；
- 不把 FSW 命令力矩称为实际交付力矩；
- 不把由 tau/Kt 推导的电流称为实测电流；
- 不将 u_current 或 u_max 等仿真物理读回放入模型输入；
- 不训练模型、不计算分类准确率、不宣称正式误报率；
- 不访问受保护 VAL/TEST/FINAL，不签发飞行/硬件安全或 Stable 资格。

## 6. 通过后的下一步

若 12 个工况通过消息来源、时钟、物理基准与回放验证，下一阶段再冻结多轮系/多初始状态/独立 seed 的多通道观测研究。轮速测量噪声、量化和丢包参数必须来自真实设备接口或明确标注为敏感性假设；在此之前不做硬件代表性声明。

## 7. R84-R3A 实际执行结果（2026-11-06）

- PR #107；GitHub Actions #42229904690：pilot-contract 与 native-r84r3 均 PASS；项目脚本治理 PASS。
- 12 个唯一原生工况 + 1 次固定 seed 回放，共 13 次真实 Basilisk 执行；每次 31 点。
- 12/12 物理基准比较通过，最大绝对差异为 0；同 seed 回放的传感器、多通道遥测和物理 QA 差异均为 0。
- IMU 噪声开关只改变 IMU 通道；六组 noise on/off 对照中的非 IMU 通道最大差异均为 0。
- 三轮轮速、三轮 FSW 命令力矩和指向误差均与各自独立 Basilisk recorder 逐点核对；传感器文件与 QA 真值分离。
- 证据 artifact #13507208414，4,780,275 bytes，SHA-256 769fcabf1d2cda41a74842653603255292dbbfc73f05c1bd7e3742ece561a775，保留至 2027-02-04。

### 通道资格边界

| 通道 | 本轮核验结论 | 资格边界 |
|---|---|---|
| IMU 三轴角速度 | 官方 ImuSensor 原始测量 | 含配置白噪声，可作为测量候选 |
| 三轮轮速 | 原生 rwSpeedOutMsg | 理想状态反馈上界；无编码器噪声、量化、丢包与硬件相关性 |
| 三轮命令力矩 | 原生 FSW rwMotorTorqueOutMsg | 命令遥测，不等于实际交付力矩 |
| 指向误差 | attTrackingError.attGuidOutMsg.sigma_BR | FSW 运行估计，不是 hub 真值 |
| 实际施加力矩 | rwOutMsgs[].u_current | 仅物理 QA |
| C++ 最大力矩权限 | ReactionWheelData[].u_max | 故障真值，仅物理 QA |
| 电机电流 | 未记录 | 当前无原生电流传感器模型；不得用 tau/Kt 冒充实测 |

研究决策：多通道来源和物理一致性小规模试验通过，但轮速仍是理想反馈上界。下一轮应先获取真实轮速传感器接口参数（采样率、量化、噪声、偏置、丢包、时钟），或明确将其作为敏感性假设，再开展多 seed、多轮系统计可观测性试验。本轮没有训练模型、没有正式数据集资格，也没有硬件/飞行或 Stable 资格。
