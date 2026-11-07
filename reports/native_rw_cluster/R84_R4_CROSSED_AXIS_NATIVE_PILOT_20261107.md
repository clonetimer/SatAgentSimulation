# R84-R4 指令轴与故障目标轮交叉原生 pilot

状态：**设计解耦与原生来源审计 PASS；仅为有界可行性试验**。先前 R2/R4 仿真用同一个 `wheel` 参数选择四脉冲指令轴与受损反作用轮，无法把已知任务指令轴与未知故障目标轮的统计作用拆开。本轮增加独立的 `excitation_axis`，保留原 R2 原生模块和既有预注册证据原样不动。

冻结配置 `configs/research/r84r4_crossed_axis_native_pilot_prereg_v1.json` 的 SHA-256 是 `9291df92fb88b068097abe8ac9d33b95b2ac532e1bb51d6be36f942dd124467d`。执行 3 个指令轴 × 3 个物理故障轮 × 3 个初始姿态 × 健康/loss50/loss75 = **81 个新 Basilisk 原生 18 秒工况**，另做一次同种子完整回放。IMU 为官方 `ImuSensor`，轮速为 R3B 未标定敏感性测量层，FSW 命令为原生 `rwMotorTorqueOutMsg`。物理 QA 与标签另存；不计算诊断性能。

本地独立审计 PASS：81 种交叉因素组合各一次、7371 个原生点、3726 个观测点、237 个显式缺失轮速值；与 R2、R4 早期 pilot 和 R4 独立队列种子均无重叠。每次脉冲的 FSW MRP 读回仅在预定指令轴非零，C++ `ReactionWheelData` 权限读回仅对所选故障轮验证。固定指令轴和初始姿态时，三个健康“名义故障轮”的物理轨迹除目标轮专属 QA 记录器字段外逐点一致。观测字段严格不含目标轮、权限或故障标签；同平台回放的 IMU、QA、观测完全一致。

本地原始证据：`reports/local_r84r4_20261107/evidence/r84r4_crossed_axis_pilot_v1/`，四个压缩 JSONL 的 SHA-256 在 manifest 中；独立收据为 `r84r4_crossed_axis_pilot_audit.json`。这些文件暂存本地，云端原生复验待执行。

下一步应在此解耦结构上预注册新的**指令轴已知、故障轮未知**统计试验，健康校准与独立健康控制必须在指令轴和初始姿态内分层，同时故障目标轮独立交叉。还应将平台、IMU 噪声和轮速测量假设列为实验因素；本 pilot 每组合仅一条种子，不足以估计误报、loss50 或 loss75 检出率。R3B 硬件测量标定与 R85 数据资格仍未完成。
