# 整星 ADCS 配对数据集建模与故障注入说明

版本：v2（2026-08-10）  
脚本：`scripts/generate_whole_spacecraft_adcs_eps_dataset.py`  
能力：`whole_spacecraft.unified_native.v1`  
流程图：`docs/assets/whole_spacecraft_adcs_eps_dataset_flow.svg`

## 1. 目标与结论

该脚本用同一个 Basilisk Process/Task 模拟整星动力学、轨道环境、ADCS、EPS、载荷、通信/数管、热控和推进，并导出运行时产生的**全部整星轨迹字段**。本阶段只对 ADCS 做单异常注入，同时生成正常数据。

数据设计为：

\[
\mathcal D=\mathcal M\times\mathcal C\times\mathcal K
\]

其中任务集 \(\mathcal M\) 有3类，条件集 \(\mathcal C\) 为1个正常条件加34个可执行ADCS单异常条件，每个组合有 \(K\in[5,10]\) 个轻微扰动样本。因此：

\[
N_{run}=3\times 35\times K
\]

默认 \(K=5\)，共525次运行；若 \(K=10\)，共1050次运行。

## 2. 本阶段部件选型

为避免同功能部件重复，数据集固定为一套可解释架构：

| 功能 | 选用部件 | 用途 | 未选用 |
|---|---|---|---|
| 姿态执行 | 4 个 Honeywell HR16 反作用轮，四面体金字塔构型 | 三轴控制并提供一度冗余 | CMG、磁力矩器 |
| 主姿态测量 | 星敏感器 + IMU | 闭环姿态与角速度输入 | — |
| 背影面粗姿态测量 | 太阳方向敏感器 + 地平/地球敏感器 | 双矢量TRIAD粗姿态、星敏失效备份 | — |
| 电源 | 太阳阵、电池、PDU、动态负载桥 | 发电、储能、配电 | — |
| 其他整星分系统 | 载荷、通信/数管、热控、推进 | 形成资源和任务耦合 | — |

太敏采用 Basilisk `CoarseSunSensor`。项目原先没有地敏部件，因此新增 `EarthHorizonSensor`：它在同一个 Basilisk 任务内读取 `SCStatesMsg`，发布机体系地心方向消息，来源在运行清单中标为 `basilisk_project_native`，不冒充官方模型。

统一运行时仍保留原有磁场/磁强计内部模块，但不把它当作选定姿态确定链。精姿态链为星敏+IMU，粗姿态链为太敏+地敏双矢量TRIAD；两者在 `AdcsSensorFusion` 中融合，星敏失效时自动切换到太敏+地敏。飞控链不实例化或订阅 `SimpleNav`。

仿真侧传感器模型和所有Basilisk传感器一样，需要读取整星状态来生成测量；关键边界是：FSW `attTrackingError.attNavInMsg` 只订阅融合器输出，不直接订阅 `SCStatesMsg` 或 `SimpleNav.attOutMsg`。

## 3. 任务场景

| mission_id | 初始指向误差 | 载荷 | 推进 | 取值依据 |
|---|---:|---|---|---|
| `attitude_acquisition` | 12°–18° | 关闭/零数据率 | 硬件实例化，推力 0 | 表示较大初始失准后的捕获过程 |
| `steady_pointing_payload` | 1°–3° | 开启，2.35–2.65 Mbit/s | 硬件实例化，推力 0 | 表示稳态精指向和载荷工作 |
| `propulsive_maneuver` | 3°–7° | 关闭 | 0.8–1.2 N，约在 40% 时刻开始，持续约 10% | 表示外扰和资源耦合较强的机动 |

所有任务都实例化推进器和贮箱，以保持整星部件图及字段模式一致。非推进任务仅把推力幅值设为 0，而不是删除推进分系统。

## 4. Monte Carlo 与共享正常基线

同一个任务 \(m\) 和样本序号 \(k\) 使用共享配对种子：

\[
s_{pair}=s_0+100000\,i_m+k
\]

其中 \(s_0\) 是命令行基础种子，\(i_m\) 是任务索引。由 \(s_{pair}\) 生成轨道初值、姿态误差、SOC、电池容量、太阳阵参数和负载参数。正常条件与全部异常条件共享这些参数：

\[
\theta_{m,k,normal}=\theta_{m,k,c},\quad \forall c\in\mathcal C
\]

故障幅值使用独立、确定性的条件种子：

\[
s_{event}=s_{pair}+10^7+10000\,i_c
\]

因此故障抽样不会反向改变正常初值。`pair_id=mission_id:mc_kk` 可直接连接正常和异常轨迹，适合差分、对比学习和因果效应评估。

样本间扰动采用均匀分布，目的是覆盖局部工作域而不是估计真实在轨先验。若后续有型号统计数据，应把均匀分布替换为实测/验收分布，并在清单中更新依据。

## 5. 反作用轮构型与姿态动力学

四轮安装方向为正四面体顶点：

\[
\mathbf g_1=\frac{1}{\sqrt3}[1,1,1]^T,\quad
\mathbf g_2=\frac{1}{\sqrt3}[1,-1,-1]^T
\]

\[
\mathbf g_3=\frac{1}{\sqrt3}[-1,1,-1]^T,\quad
\mathbf g_4=\frac{1}{\sqrt3}[-1,-1,1]^T
\]

安装矩阵 \(G_s=[\mathbf g_1,\mathbf g_2,\mathbf g_3,\mathbf g_4]\) 将轮系力矩映射到机体系。简化的整星转动方程为：

\[
\mathbf I\dot{\boldsymbol\omega}+\boldsymbol\omega\times(\mathbf I\boldsymbol\omega+G_s\mathbf h_w)
=\mathbf L_{ext}-G_s\mathbf u_w
\]

符号：\(\mathbf I\) 为整星惯量矩阵，\(\boldsymbol\omega\) 为本体角速度，\(\mathbf h_w\) 为轮角动量，\(\mathbf u_w\) 为轮电机力矩，\(\mathbf L_{ext}\) 为外力矩。

MRP 反馈控制使用 Basilisk `mrpFeedback` 和 `rwMotorTorque`。概念形式为：

\[
\mathbf L_r=-K\boldsymbol\sigma_{BR}-P\boldsymbol\omega_{BR}
\]

默认 \(K=3.5\)、\(P=30\)，并通过轮安装矩阵分配到四个反作用轮。四轮构型只在本数据集参数中启用，运行时仍保留 `orthogonal_3` 兼容模式，避免破坏其他项目场景。

## 6. 传感器模型

### 6.1 星敏与 IMU 主链

星敏输出四元数 \(\mathbf q_{BN}\)，转换为 MRP \(\boldsymbol\sigma_{BN}\)。IMU 测量为：

\[
\tilde{\boldsymbol\omega}=\boldsymbol\omega+\mathbf b_g+\mathbf n_g
\]

其中 \(\mathbf b_g\) 为陀螺偏置，\(\mathbf n_g\) 为噪声。故障注入发生在原始 IMU 消息与 `AdcsSensorFusion` 之间，因而会传播到姿态误差、控制力矩、轮速及后续资源状态。

### 6.2 背影面太敏

太敏法向固定为：

\[
\mathbf n_{css}^{B}=[0,0,-1]^T
\]

视场半角默认 80°，理想输出近似：

\[
y_{css}=f_{eclipse}\max(0,\mathbf n_{css}^{B}\cdot\hat{\mathbf s}^{B})
\]

其中 \(f_{eclipse}\in[0,1]\) 为遮挡因子，\(\hat{\mathbf s}^{B}\) 是机体系太阳方向。

### 6.3 地敏

由惯性系位置 \(\mathbf r_{BN}^{N}\) 得到地心方向：

\[
\hat{\mathbf e}^{N}=-\frac{\mathbf r_{BN}^{N}}{\|\mathbf r_{BN}^{N}\|},\qquad
\hat{\mathbf e}^{B}=C_{BN}(\boldsymbol\sigma_{BN})\hat{\mathbf e}^{N}
\]

当前模型为理想几何方向，不含地平辐射、遮挡、噪声或安装误差；这些属于后续需要标定的模型扩展。

## 7. 可执行的单异常条件

本数据集的“支持”严格指：统一整星运行时已实现、校验器接受、注入器会改变消息或模型参数、遥测可观测。每个异常样本只含一个事件。

| condition_id | 类别 | 运行时 effect | 参数与范围 | 直接作用 |
|---|---|---|---|---|
| `adcs_rw_jamming` | fault | `adcs_rw_jamming` | 轮号 0–3；制动力矩 0.18–0.20 N·m；锁定容差 0.15–0.35 rad/s | HR16额定力矩的90%–100%；严重卡死 |
| `adcs_rw_motor_failure` | fault | `adcs_rw_motor_failure` | 轮号 0–3；力矩比例 0 | 部件目录 `motor_open` 的硬失效定义 |
| `adcs_gyro_bias_step` | fault | `gyro_bias_step` | 随机单轴，0.001–0.010 deg/s，即3.6–36 deg/h | NASA检测量级锚点到其10倍灵敏度范围 |
| `adcs_gyro_noise_increase` | degradation | `gyro_noise_increase` | 噪声倍数 8–12 | 项目L2 `noise_burst` 的8倍锚点及有界离散 |
| `adcs_rw_bearing_seizure` | degradation | `rw_friction_degradation` | 轮号 0–3；黏性阻尼约0.000239–0.000955 N·m·s | 在200 rpm参考轮速下等效0.005–0.020 N·m拖曳力矩 |
| `adcs_rw_torque_authority_loss` | degradation | `adcs_rw_torque_authority_loss` | 轮号 0–3；剩余力矩0.25–0.70 | 下限对应部件目录轴承咬死0.25力矩因子 |
| `adcs_rw_speed_limit` | constraint | `adcs_reaction_wheel_speed_limit` | 轮号0–3；上限5–9 rad/s；超速制动增益0.015–0.025 N·m·s | 为保证每个轮都触发保护路径的灵敏度约束，不是硬件故障统计 |

正常条件 `nominal` 没有事件，`effect=nominal`，`is_nominal=1`。

完整34个异常条件覆盖：反作用轮/执行与约束6个、IMU及其L2签名9个、星敏8个、太敏8个、ADCS子系统兼容故障3个；其中若干目录名称映射到同一物理条件（例如 `motor_open` 与电机失效），所以按唯一运行条件计数为34。逐类型映射保存在 `dataset_manifest.json.fault_type_to_condition`。

故障起始时刻为：

\[
t_f\sim U(0.22T,0.32T)
\]

事件持续到仿真结束。发生时刻区间是试验设计变量，不代表故障时间概率分布。

### 7.1 标定原则和可信等级

标定版本为 `reference-informed-v1`，遵循下列来源优先级：

1. 当前仿真实例真正使用的Basilisk/部件额定参数；
2. 厂商、NASA/ESA任务资料或试验报告；
3. 项目部件故障目录中的机制与效应锚点；
4. 缺少统计数据时，用灵敏度试验给出边界，并明确标记为工程判断。

这与NASA Orion GN&C FDIR公开方法一致：优先采用FMEA、厂商和历史数据；多数签名缺数据时，使用工程判断确定边界。每个条件在清单中保存：

- `calibration_basis`：范围和换算说明；
- `calibration_source_ids`：来源编号；
- `calibration_confidence`：`high`、`medium`或`low`；
- `severity_band`：故障严重度语义。

可信等级不是发生概率：`high`表示效应和值可直接追溯到当前模型或明确部件定义；`medium`表示有文献/项目锚点但做了跨任务或灵敏度外推；`low`表示主要为可观测性试验设计。

### 7.2 反作用轮标定

Basilisk HR16参数为：

\[
u_{max}=0.200\ \mathrm{N\,m},\quad
\Omega_{max}=6000\ \mathrm{rpm}=628.319\ \mathrm{rad/s},\quad
T_{C}=5\times10^{-4}\ \mathrm{N\,m}
\]

卡死制动力矩由额定力矩归一化得到：

\[
T_{brake}\in[0.9,1.0]u_{max}=[0.18,0.20]\ \mathrm{N\,m}
\]

轴承故障采用项目L2目录的0.020 N·m严重摩擦签名作为上界，并取0.005 N·m为渐进退化下界。以200 rpm为参考速度：

\[
\omega_{ref}=200\frac{2\pi}{60}=20.944\ \mathrm{rad/s},\qquad
c=\frac{T_{drag}}{\omega_{ref}}
\]

得到：

\[
c\in[2.387\times10^{-4},9.549\times10^{-4}]\ \mathrm{N\,m\,s}
\]

这避免了旧范围在40 rad/s时产生0.4 N·m拖曳、超过额定力矩两倍的问题。

### 7.3 陀螺标定

NASA公开SLS INS研究给出3.6 deg/h的故障检测量级示例，采用它作为下界，并用10倍值作为短时仿真的严重灵敏度上界：

\[
\Delta b_g\in[3.6,36]\ \mathrm{deg/h}
=[0.001,0.010]\ \mathrm{deg/s}
\]

这比旧范围0.20–0.80 deg/s降低20–800倍，更适合物理故障数据。生产配置在故障后至少保留204 s，因此若不被融合与闭环补偿，0.001–0.010 deg/s偏置可积累约0.20–2.04 deg角度差；上界仍属于工程敏感性边界，而不是在轨概率分布。噪声增加以项目L2目录的8倍签名为锚点，取8–12倍，而非旧范围8–40倍。

## 8. 故障方程

### 8.1 电机失效和力矩能力下降

对目标轮 \(j\)：

\[
u_j^{app}=\operatorname{sat}(\alpha_j u_j^{cmd},-u_{j,max}^{eff},u_{j,max}^{eff})
\]

电机开路时 \(\alpha_j=0\)；部分能力下降时：

\[
u_{j,max}^{eff}=\rho_Tu_{max},\quad \rho_T\in[0.25,0.70]
\]

### 8.2 轴承摩擦和卡死

摩擦退化使用：

\[
u_{drag,j}=-c_j\omega_j
\]

其中 \(c_j\in[2.387\times10^{-4},9.549\times10^{-4}]\ \mathrm{N\,m\,s}\)。卡死管理器还根据轮速方向施加制动力矩，接近锁定容差时抑制运动。

### 8.3 轮速约束

\[
|\omega_j|\le \omega_{max,j}^{eff}
\]

接近/超过有效上限时，继续加速方向的力矩命令被限制。

### 8.4 陀螺偏置和噪声

偏置阶跃：

\[
\tilde{\boldsymbol\omega}=\boldsymbol\omega+H(t-t_f)\Delta\mathbf b+\mathbf n
\]

噪声增加：

\[
\mathbf n\sim\mathcal N(\mathbf0,(\lambda\sigma_0)^2I),\quad \lambda\in[8,12]
\]

其中 \(H\) 是 Heaviside 函数，\(\sigma_0\) 默认 \(10^{-5}\ \mathrm{rad/s}\)。

### 8.5 标定来源

1. [Basilisk `simIncludeRW` HR16模型](https://avslab.github.io/basilisk/_modules/simIncludeRW.html)：0.200 N·m、6000 rpm及0.0005 N·m库仑摩擦。
2. [NASA Orion GN&C FDIR设计](https://ntrs.nasa.gov/api/citations/20160001200/downloads/20160001200.pdf)：故障签名建模、数据来源优先级和缺数据时的工程边界方法。
3. [NASA SLS INS阈值调度研究](https://ntrs.nasa.gov/api/citations/20180005156/downloads/20180005156.pdf)：陀螺故障检测量级示例。
4. [NASA Cassini反作用轮摩擦参数辨识](https://ntrs.nasa.gov/citations/20090040058)：摩擦随轮速变化以及低/高速区间分别辨识。
5. [ESA反作用轮退化说明](https://sdup.esoc.esa.int/documents/download/ESA_Space_Debris_Mitigation_Compliance_Verification_Guidelines.pdf)：润滑/轴承退化到干摩擦、黏性摩擦、功率和温度的传播。
6. 项目 `src/components/reaction_wheel/faults.py` 和 `src/components/imu/faults.py`：0.020 N·m轴承咬死、0.25剩余力矩和8倍噪声签名。

## 9. 故障目录审计与边界

对 `src/components` 和 `src/subsystems/adcs` 的检查结果写入每个数据集清单。声明故障不等于整星可执行故障。

### 9.1 所选部件目录声明

- 反作用轮：`rw_jamming`、`rw_motor_failure`、`rw_bearing_seizure`、`bearing_seizure`、`motor_open`、`speed_sensor_fault`。
- IMU：`signal_loss`、陀螺/加速度计偏置漂移、噪声增加、`stuck_at_zero`、`bias_step`、`axis_dropout`、`noise_burst`。
- 星敏：`signal_loss`、`bias_drift`、`accuracy_loss`、`fov_obstruction`、`stuck_at_last`、`blinding`、`dropout`、`misalignment`。
- 太敏：`signal_loss`、`bias_drift`、`noise_increase`、`saturation`、`eclipse_blindness`、`false_eclipse`、`cell_failure`、`contamination`。

### 9.2 ADCS 子系统预置场景

- `rw_bearing_seizure`：以单个 `rw_friction_degradation` 事件纳入。
- `mtb_cmg_actuator_fault`：不纳入，因为选定架构没有 MTB/CMG，且原场景为多故障。
- `sensor_dropout_bias_fault`：不纳入，因为是 IMU/星敏/太敏/磁强计组合故障，并且相应注入器未接入统一整星运行时。
- `combined_adcs_fault`：不纳入，因为要求单故障执行。

星敏、太敏的大部分目录故障，IMU 掉线/加速度通道故障和轮速传感器故障目前只“声明”而未接到统一整星运行时。脚本不会为它们制造假样本；清单的 `declared_not_executable` 给出具体原因。若下一阶段要覆盖它们，应先实现消息级注入器、定义有效性状态并补齐可观测性测试。

## 10. 依赖与传播关系

主要闭环为：

\[
\text{轨道/姿态状态}\rightarrow
\begin{cases}\text{星敏+IMU精姿态}\\\text{太敏+地敏TRIAD粗姿态}\end{cases}
\rightarrow\text{融合}\rightarrow
\text{姿态误差}\rightarrow\text{MRP控制}\rightarrow\text{轮力矩}\rightarrow\text{整星姿态}
\]

跨分系统路径为：

\[
\text{指向误差}\rightarrow\text{载荷/通信门控}\rightarrow\text{数据产生与下传}\rightarrow\text{存储量}
\]

\[
\text{ADCS活动}\rightarrow\text{功率桥}\rightarrow\text{电池净功率/SOC}\rightarrow\text{PDU保护与任务许可}
\]

\[
\text{各负载功耗}\rightarrow\text{热输入}\rightarrow\text{节点温度}\rightarrow\text{热保护/模式门控}
\]

\[
\text{推进推力}\rightarrow\text{整星动力学},\qquad
\text{推进活动}\rightarrow\text{EPS负载与推进节点温度}
\]

典型故障传播：

- 陀螺偏置/噪声 → 估计角速度异常 → 控制命令变化 → 轮速和指向误差变化 → 载荷/通信活动及资源状态变化。
- 轮卡死/电机失效/力矩下降 → 已施加力矩偏离命令 → 姿态误差增大 → 任务门控变化。
- 轮摩擦/轮速限制 → 轮动态与可用控制裕度下降 → 姿态闭环性能退化。

重要边界：当前 `AdcsControlPowerBridge` 读取的是 `rwMotorTorque` 的**故障前原始命令**和轮速，不是故障管理器后的已施加力矩。因此轮故障对 EPS 的影响主要通过姿态/任务门控及轮速演化间接传播；不能把现有结果解释为电机卡死电流或摩擦热的高保真电气传播。电机电流字段同样由原始命令和力矩常数换算：

\[
I_j=\operatorname{clip}\left(\frac{u_j^{cmd}}{K_t},-I_{max},I_{max}\right)
\]

若要研究卡死过流和局部发热，应把已施加力矩、电机效率、堵转电流及摩擦耗散显式接入 EPS/热控。

## 11. 整星输出字段

v2 不再只筛选 ADCS/EPS。`telemetry.csv` 包含数据集标签和运行时全部轨迹字段：

- 标签：`sample_id`、`pair_id`、`mission_id`、`condition_id`、`event_category`、`effect`、`is_nominal`、`event_active`。
- 轨道/环境：`orbit.*`、日蚀因子。
- ADCS：指向误差、角速度、陀螺偏置/噪声、星敏四元数、太敏输出、地敏方向、四轮速度/命令/已施加力矩/电流/有效限制。
- EPS：太阳阵功率、电池能量/容量/SOC/净功率、各负载和 PDU 状态。
- 载荷、通信/数管：活动、产生/下传速率、BER/PER、存储量、链路状态。
- 热控：各节点温度、安全状态、热输入、散热器拒热和开关状态。
- 推进：燃烧许可/活动、燃料质量与流率、推力、用电。

字段集合写入 `dataset_manifest.json.telemetry_fields`，实际烟测为 160 余个原始整星字段；字段数以所用项目版本的清单为准，不硬编码在训练程序中。

## 12. 数据目录与可追溯性

```text
output_root/
  dataset_manifest.json
  sample_index.csv
  mission_id/
    condition_id/
      sample_00/
        telemetry.csv
        sample_metadata.json
```

`sample_metadata.json` 保存完整 TaskSpec、配对种子、事件参数、整星参数和仿真摘要。根清单保存架构选择、故障目录审计、字段全集和模块覆盖证据。

## 13. 使用方法

在项目根目录使用项目解释器：

```bash
.venv/bin/python scripts/generate_whole_spacecraft_adcs_eps_dataset.py --dry-run
```

默认完整生成：

```bash
.venv/bin/python scripts/generate_whole_spacecraft_adcs_eps_dataset.py \
  --samples-per-combination 5 \
  --duration-s 300 --step-s 0.1 --sample-s 0.5 \
  --output data/generated/whole_spacecraft_adcs_paired_dataset
```

后台生成与断点续跑：

```bash
nohup env PYTHONUNBUFFERED=1 MPLCONFIGDIR=/tmp/matplotlib-sat \
  .venv/bin/python scripts/generate_whole_spacecraft_adcs_eps_dataset.py \
  --resume --samples-per-combination 5 \
  --duration-s 300 --step-s 0.1 --sample-s 0.5 \
  --output data/generated/whole_spacecraft_adcs_paired_dataset \
  > data/generated/whole_spacecraft_adcs_paired_dataset.nohup.log 2>&1 &
```

每完成一个样本都会原子更新 `telemetry.csv`、`sample_metadata.json`、`dataset_manifest.progress.json` 和 `sample_index.progress.csv`。再次执行完全相同的命令并带 `--resume` 时，生成器会校验TaskSpec、遥测表头和非空行，只跳过完整且与当前计划匹配的样本；半写或参数不匹配的样本会重跑。

先做一个任务、一个异常及其正常基线的短验证：

```bash
.venv/bin/python scripts/generate_whole_spacecraft_adcs_eps_dataset.py \
  --missions attitude_acquisition \
  --conditions nominal adcs_rw_jamming \
  --samples-per-combination 5 \
  --duration-s 10 --step-s 0.2 --sample-s 1.0 --allow-short-run \
  --output /tmp/sat_dataset_smoke
```

输出目录必须为空，防止覆盖已有长时间生成结果。

### 13.1 数据集时间基准

生产数据统一采用 `duration_s=300 s`、`step_s=0.1 s`、`sample_s=0.5 s`。每条样本包含约601个对齐遥测点和3000个Basilisk积分/控制周期；每5个控制周期记录一次遥测。单一时间网格保证不同任务、正常/故障配对及蒙特卡洛样本可以直接对齐。

异常开始时刻从总时长的28%–32%采样，即84–96 s。因此至少有84 s正常基线和204 s故障后传播窗口。前者覆盖姿态捕获初始瞬态并支持配对基线检验；后者允许陀螺漂移、星敏偏置、轮系摩擦等渐变故障积累为可辨识的闭环响应。0.1 s内部步长用于解析ADCS控制回路，0.5 s采样周期则保留IMU、轮速和控制力矩瞬态，同时避免导出全部积分点。

生成器强制 `sample_s/step_s` 和 `duration_s/sample_s` 为整数，并在生产模式要求步长不大于0.1 s、采样周期不大于0.5 s、故障前窗口不少于60 s、故障后窗口不少于180 s且每条不少于600行。`--allow-short-run` 仅供单元测试和烟测使用，不应加入正式生成命令。

单故障注入效应验证：

```bash
.venv/bin/python scripts/validate_adcs_fault_injection_effects.py \
  --duration-s 12 --step-s 0.2 --sample-s 1 \
  --output data/generated/adcs_single_fault_validation
```

该工具为34个异常条件各生成一次与正常样本同种子的配对轨迹，检查故障前所有数值字段完全一致，并计算故障后的指向误差、轮速、已施加力矩、IMU、星敏、太敏、有效性及融合回退指标。当前报告为34/34通过，最大故障前数值差为0。首次试验发现轮速约束没有触发，随后把约束改为5–9 rad/s并加入0.015–0.025 N·m·s超速制动增益；复测后该场景产生约1.29°指向差和18.98 rad/s轮速差。

## 14. 质量检查

生成前后应满足：

1. 计划数量等于 \(|M||C|K\)。
2. 正常样本无事件；异常样本恰好一个事件。
3. 同一 `pair_id` 的 `spacecraft_parameters` 完全一致。
4. 轮号仅为 0–3，运行时确实输出第 4 轮字段。
5. 所有整星模块组都通过 `module_coverage`。
6. `telemetry_fields` 同时含有 orbit、adcs、eps、payload、comm、data、thermal、propulsion 前缀。
7. 太敏和地敏消息被实例化、连接并记录。
8. 长任务生成前先运行 dry-run、单元测试和短烟测。
9. `fault_effect_report.json` 中所有条件为 `PASS`，且 `pre_fault_max_numeric_delta` 不超过数值容差。

## 15. 当前限制与后续扩展

- 地敏是理想几何模型，尚无噪声、失效、地平辐射和视场模型。
- 太敏/地敏已参与融合和星敏失效备份，但当前向量传感器仍是理想几何模型；需用具体器件噪声、视场和安装误差继续校准。
- 地敏项目中尚无独立故障目录，因此没有虚构地敏故障类型。
- 标定是“参考资料约束+项目模型换算”，仍不是HR16或某型IMU的在轨故障概率分布；低可信度参数必须与硬件数据继续校准。
- 本阶段仅做单异常；多故障组合需要单独的数据标签、冲突规则和可辨识性设计。
- 若扩展 EPS 故障，应在条件维单独增加并继续保持正常配对，而不是与 ADCS 故障默认叠加。
