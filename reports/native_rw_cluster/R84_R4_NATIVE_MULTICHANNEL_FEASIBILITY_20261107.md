# R84-R4 新原生多通道可行性试验

状态：**已执行有界原生试验；仅证明数据链和物理覆盖可行，不计算诊断效果或正式数据资格**。预注册配置 `configs/research/r84r4_native_multichannel_feasibility_prereg_v1.json` 在运行前写入，SHA-256 `df8d99ec0f869b728a6e771d4b9d15f897b0f4fe6baea049e9348575e5c068c7`。

本机 `bsk==2.11.0+satfix2` 调用现有 R84-R2 原生模块，真正执行 3 个轮系×3 个初始姿态×无激励/四脉冲×健康/loss50/loss75 = **54 个唯一 18 秒工况**，另做一次同 seed 精确回放，共 55 次、每次 91 个原生记录点。54 个新种子与已封存 R2 归档种子集逐一比对，互不重叠。

原生 `ImuSensor.sensorOutMsg.AngVelPlatform` 保留在独立 IMU 文件；`rwSpeedOutMsg.wheelSpeeds` 经 R3B 显式敏感性测量层生成每次 46 点的三轮观测；`rwMotorTorqueOutMsg.motorTorque` 作为 FSW 命令遥测，绝不写成实际施加力矩。物理 QA 和故障标签仅在独立 QA/工况清单。未从 hub 真值推造指向误差或电机电流。

本地证据目录 `reports/local_r84r4_20261107/evidence/r84r4_native_feasibility_v1/` 的 manifest 为 PASS，四个原始文件均有 SHA-256。独立审计 `reports/local_r84r4_20261107/evidence/r84r4_native_feasibility_audit.json` 为 PASS：55 次原生执行、5005 原生点、2530 观测点、141 个显式缺失轮速值（含回放）；逐点 IMU/FSW 命令对应原生记录、观测字段严格白名单、同 seed 的 IMU/QA/观测完全一致。

本轮测量参数（如 0.01 rad/s 分辨率、0.005 rad/s 高斯噪声和 2% 丢包）仍属**未标定敏感性假设**。每物理上下文仅一条唯一 seed，不能据此估计误报率或 fault 检出。正式 R4 需要额外的互斥健康校准、独立健康控制与 loss50/loss75 重复 seed，并预先锁定多通道特征、阈值、样本单位和失败处理；随后逐上下文报告效果。R85 必须另行做测量真实性和数据资格评估。
