# P9-O3：整星统一运行图事件证据闭环

日期：2026-11-19  
基线：P9-O2 squash merge `7207244a660db732ea321a0c9c64b28df0912c08`  
状态：O3-A 已合入 main（PR #188，merge commit `a96d7feddbb051cf0cf590a3f8c5e4679eed348a`），P9-G 同 head PASS；O3-B 整星级物理 A/B 与 direct recorder evidence 正在实施，O3-C/D 待完成。

## 目标

在 P9-O2 已完成 EPS、Comm/Data 独立分系统事件准入后，收敛 `whole_spacecraft.unified_native.v1` 的整星事件暴露边界。整星已有 Basilisk 统一运行图和跨分系统耦合，但事件目录比正式 effect-evidence registry 更宽；不能将独立分系统或直接 adapter 测试等同于整星耦合证据。

## 当前审计基线（2026-11-19）

- Capability: `whole_spacecraft.unified_native.v1`，`trust_level=engineering_preview`、`status=agent_ready_candidate`。
- `modes.*.effects` 暴露 14 类事件：fault 7、degradation 5、constraint 2。
- 当前 effect-evidence registry 对该 Capability 有 6 个 `VERIFIED_NATIVE_EFFECT` profiles：
  - fault：`payload_instrument_off`、`comm_data_downlink_link_loss`、`eps_battery_capacity_loss`
  - degradation：`solar_panel_efficiency_loss`、`thermal_radiator_rejection_loss`
  - constraint：`power_safe_mode_threshold`
- 其余 8 个事件尚无该 Capability 自身的 `VERIFIED_NATIVE_EFFECT` profile：
  - `adcs_rw_jamming`、`adcs_rw_motor_failure`、`gyro_bias_step`
  - `gyro_noise_increase`、`rw_friction_degradation`、`adcs_reaction_wheel_speed_limit`
  - `propulsion_thruster_ignition_failure`、`propulsion_burn_impulse_loss`
- 现有测试证据并不等价：`test_unified_native_event_migration.py` 覆盖 6 个整星事件；`test_whole_propulsion_events.py` 覆盖 2 个推进事件；ADCS 相关测试主要验证 ADCS 独立分系统。尚不能据此声称 14 个整星事件均已完成 sealed Run Bundle A/B 与浏览器验收。
- `form_schema.py` 当前对 ADCS/EPS/Comm-Data 有 guided allowlist，但未对 whole-spacecraft 应用同类 allowlist；因此普通向导可能展示无整星 effect-evidence profile 的事件。

## O3-A：收紧普通向导事件目录

1. 在服务端 `GUIDED_EFFECT_ALLOWLISTS` 中，为 whole-spacecraft 增加精确 allowlist，只显示上述 6 个已有正式 profile 的事件。
2. 在前端 `addEffect` 增加同一 Capability + event-kind allowlist，避免 DOM/状态绕过。
3. 不删除底层运行时实现，不修改现有 advanced TaskSpec/数据集内部接口；本阶段只收敛普通向导目录。直接 TaskSpec 的准入边界需在 O3-B 独立审计，不能把 UI 隐藏误称为运行时拒绝。
4. 增加 form-schema 契约测试：fault 3、degradation 2、constraint 1；8 个未验证事件不得出现在 guided catalog。
5. 增加真实 Chromium 目录测试：六个已验证事件可见，8 个未验证事件不可见；普通 nominal whole-spacecraft Run 路径不回归。

## O3-B：补齐整星级物理 A/B 与证据 profiles

对剩余事件逐项执行正式 TaskSpec nominal/injected 配对：
- 官方 Basilisk 2.11.0；
- SUCCEEDED、validation PASS、sealed、integrity PASS；
- runtime injection 直接应用成功，unsupported/failed application 为 0；
- 整星统一运行图的直接 recorder 物理量与 nominal 参数参照；
- 故障结束恢复或明确记录保护/安全模式状态；退化满足时间语义；
- 通过后才新增 `VERIFIED_NATIVE_EFFECT` profile 并扩展 allowlist。

优先顺序：
1. 整星 ADCS：卡滞、电机失效、陀螺偏置/噪声、轮摩擦、轮速约束；必须验证姿态闭环和资源耦合，不复用 subsystem 证据冒充 whole evidence。
2. 整星推进：点火失败、冲量退化；核查推力、推进剂、功耗和轨道响应的直接 recorder。
3. 对现有 6 个 profiles 做断言强度复核，尤其确认电池容量、太阳阵列和散热退化不仅改变 ratio 字段，还改变对应物理输出。

## O3-C：整星 guided browser journeys

- Nominal whole-spacecraft finite Run。
- 每个新增 verified event 至少一个真实 Chromium 用户旅程。
- 验证对象选择、事件隔离、TaskSpec 校验、异步 Basilisk Run、effect evidence、直接遥测、曲线、FMEA、sealed Run Bundle 和 Run Center 重开。
- 不把单纯目录渲染或 fixture-backed API 交互计为真实运行验收。

## O3-D：归档与准入

- JUnit XML 精确记录各事件旅程；上传整星配对 A/B 与 Run Bundle 证据。
- 所有 O3 物理证据与浏览器旅程必须来自同一最终 PR head。
- 未通过事件保持隐藏；单个事件失败不得阻塞其他已验证事件。
- 整星 Capability 保持 `agent_ready_candidate`，直到 evidence、browser、运行边界和 claim limitations 全部闭合；不得仅因 6 个事件通过就升级整体状态。

## 明确不在 O3-A

- 不重写 UnifiedNativeRuntime，不新增第二套 TaskSpec/Run 系统。
- 不扩展 RF/BER/PER、地面站过站或轨道几何模型。
- 不宣称飞行硬件标定或 flight validation。
- 不把 O2 子系统 A/B 结果复用为整星 A/B 证据。


## O3-B 实施细化与物理边界（2026-11-19）

### 事件准入矩阵

| 事件 | 直接物理量/命令路径 | A/B 关键断言 |
|---|---|---|
| adcs_rw_jamming | `adcs.rw.effective_torque_ratio_0`、`adcs.control.applied_torque_nm_0` | 驱动权限归零，正轮速时出现有界反向制动力 |
| adcs_rw_motor_failure | `adcs.rw.effective_max_torque_nm_0`、`adcs.control.applied_torque_nm_0` | 轮端可用扭矩和实际命令均归零 |
| gyro_bias_step | `adcs.sensor.gyro_bias_norm_rad_s`、`adcs.sensor.gyro_measured_rad_s_x` | 测量偏置与 nominal 对照，姿态估计链保持连接 |
| gyro_noise_increase | `adcs.sensor.gyro_noise_norm_rad_s` | 活动期噪声输出高于 nominal；使用固定 seed 保证可复现 |
| rw_friction_degradation | `adcs.rw.effective_drag_nms_0`、`adcs.rw.speed_rad_s_0` | 增加轮端摩擦力矩并比较轮速响应 |
| adcs_reaction_wheel_speed_limit | `adcs.rw.effective_max_speed_rad_s_0`、`adcs.control.applied_torque_nm_0` | 设置 15 rad/s 限速；初始 RW1 设为 100 rad/s，验证超速时制动命令 |
| propulsion_thruster_ignition_failure | `propulsion.burn_active`、`propulsion.total_thrust_n`、`propulsion.fuel_mass_kg` | 点火命令归零、无推力、燃料不消耗 |
| propulsion_burn_impulse_loss | `propulsion.burn_command_on_time_s`、推力、燃料、轨道半径 | 单次燃烧 on-time 按剩余冲量比例缩短，推进剂消耗与轨道响应低于 nominal |

### 成对运行策略

- 采用两个可复用 nominal 基线（whole ADCS、whole propulsion）和八个 injected Run Bundle，共 10 个正式 Basilisk 运行。
- 每个 injected Run 都要求官方 Basilisk 2.11.0、SUCCEEDED、validation PASS、sealed、integrity PASS、单事件 injection evidence PASS、unsupported/failed direct application 为 0。
- ADCS 六个事件使用相同姿态初始误差、轮组构型和轮端初始速度；speed-limit 场景将 RW1 初始速度提高至 100 rad/s，确保限速制动真正被激活。
- 推进两事件使用同一 5 s burn start、4 s nominal on-time、相同初始燃料/SOC；点火失败检查零推力/零耗剂，冲量损失检查 burn on-time、耗剂和最终轨道半径相对 nominal 的变化。
- 为直接审计推进命令，新增 `propulsion.burn_command_on_time_s` 与 `propulsion.nominal_burn_on_time_s` 两个逐样本字段；它们是项目原生 burn controller 的命令证据，实际物理结果仍以 Basilisk 推力、燃料和轨道 recorder 为准。

### 整星模型来源边界

- 当前 whole-spacecraft 统一图有 4 个 engineering-proxy power bridges（ADCS、payload、comm、heater）；启用推进时增加第 5 个 propulsion power bridge。它们参与电源活动耦合，不是官方 Basilisk 模块。
- O3-B 可以验证八种事件在官方 Basilisk 统一运行图中的直接命令/物理效果，但不得宣称整星所有功率桥均为官方 Basilisk 物理模型。
- 每个 profile 的 claim limitations 必须说明 project-native event controller 与 engineering-proxy power bridge 的边界；整星 Capability 继续保持 `agent_ready_candidate` / `engineering_preview`。
