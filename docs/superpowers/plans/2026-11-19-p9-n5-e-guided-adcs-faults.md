# P9-N5-E：ADCS 向导受控故障/退化入口

日期：2026-11-19
状态：实施中；仅开放 N5-D 已完成官方 Basilisk 2.11.0 物理 A/B 与 evidence profile 的五类事件。

## 目标

在“快速仿真 → 分系统独立仿真 → ADCS”中开放受控故障/退化配置。普通用户可以在对话框里选择事件类型、目标轮/传感器参数、起止时间，完成校验、真实有限时长运行、结果查看、历史回放和完整 Run Bundle 导出。

本阶段不开放所有注册事件，不开放运行约束，不开放未完成物理 A/B 的 ADCS 效应。

## 唯一允许进入 ADCS 向导的事件

| 类别 | Effect ID | 用户含义 | 必要参数 |
|---|---|---|---|
| 故障 | `adcs_rw_jamming` | 反作用轮卡滞 | wheel_index、brake_torque_nm、start_s、end_s |
| 故障 | `adcs_rw_motor_failure` | 反作用轮电机完全失效 | wheel_index、start_s、end_s |
| 故障 | `gyro_bias_step` | 陀螺偏置突变 | bias_step_deg_s、start_s、end_s |
| 退化 | `adcs_rw_torque_authority_loss` | 反作用轮剩余力矩权限下降 | wheel_index、torque_scale、start_s、end_s |
| 退化 | `gyro_noise_increase` | 陀螺噪声增大 | noise_scale、start_s、end_s |

其他已注册的 `rw_friction_degradation`、`adcs_reaction_wheel_speed_limit` 和任意 constraint 均不出现在 ADCS 向导。前端与服务端表单 schema 使用同一显式 allowlist；不能仅靠隐藏 HTML 选项实现限制。

## 实施

### E1：表单 schema 与参数语义

1. 在 `form_schema.py` 为 `subsystem.adcs_unified_native.v1` 增加唯一五事件 allowlist，`event_catalog` 仅返回上述事件。
2. 修正事件参数字段与 runtime 真实读取键一致：`gyro_bias_step.bias_step_deg_s`、`gyro_noise_increase.noise_scale`；补齐 `adcs_rw_jamming.brake_torque_nm`。
3. 保持 motor failure 为二值故障，不添加 torque_scale；部分力矩能力只用 torque authority loss 表示。
4. 加入 schema 单测：类别、Effect ID、参数名、默认值、单位、范围必须与 N5-D 运行时一致；其他 ADCS 效应不得出现在 event_catalog。

### E2：向导交互

1. 仅当 `goal=subsystem_native` 且 capability 为 ADCS unified native 时显示“故障与事件”面板。
2. 事件类型只显示“故障”和“退化”；约束选项隐藏。
3. 显示明确提示：五类事件有官方 Basilisk 2.11.0 物理 A/B 证据，但仍是工程仿真模型，不代表飞行硬件标定或飞行验证。
4. 参数表单显示单位、范围、默认值和说明；起止时间必须落在仿真时长内且 end_s > start_s。
5. 添加事件时只允许从服务端返回的五类事件目录选择；其他 effect ID 不接受。
6. 保留原有 TaskSpec → preflight → planner → registered adapter → sealed Run Bundle 执行链。

### E3：真实 Chromium 用户旅程

1. 选择 ADCS 分系统，确认事件面板可见。
2. 断言故障目录严格为 3 项，退化目录严格为 2 项，约束目录为空。
3. 选择一类 fault 和一类 degradation，编辑目标参数与起止时间；检查提交的 canonical TaskSpec 保留正确 event ID/参数。
4. 至少执行一个真实事件 Run，验证 SUCCEEDED、validation PASS、sealed、integrity PASS、runtime effect PASS。
5. 核验 event.active_effects、关键 recorder 曲线、FMEA 条目和 effect evidence；下载完整 Run Bundle 并逐文件核验 SHA-256。
6. 从运行中心重开同一 Run，确认事件和结果 provenance 保持一致。

## 物理边界

- 五类事件的物理效果由 N5-D 的官方 Basilisk 2.11.0 A/B 与 VERIFIED_NATIVE_EFFECT profile 支持。
- `operator.effects[].verification` 继续使用 schema 允许的 `declared`；物理资格来自 effect evidence profile，不写入不存在的 `verified` 状态。
- 只开放 nominal/fault/degradation finite offline；不开放 ADCS realtime、飞行软件 FDIR、MEKF/Kalman、整星惯量、安装 CAD 或 EPS/热控/通信耦合。
- 未通过独立 A/B 的摩擦退化、轮速约束等事件继续隐藏。

## 验收门槛

- 同一最终 PR head：表单 schema 单测、事件参数合同、官方 Basilisk 2.11.0 物理回归、Chromium 真实用户旅程和共享 CI 全部通过。
- Chromium 必须实际从 UI 添加事件并提交 canonical TaskSpec，不以 API 直调或 DOM 文本存在替代。
- 真实 Run 的 injection evidence 必须 PASS，且 Run Bundle seal/integrity PASS。
- 未列入 allowlist 的事件不能通过 ADCS 向导 event catalog 进入。

## N5-E 实施记录（2026-11-19）

- 服务端 form schema 对 `subsystem.adcs_unified_native.v1` 增加五事件显式 allowlist；其他已注册的摩擦退化与轮速约束不进入该向导。
- 修正 runtime 参数映射：`gyro_bias_step.bias_step_deg_s`、`gyro_noise_increase.noise_scale`；补齐 `adcs_rw_jamming.brake_torque_nm`。
- ADCS subsystem 目标显示故障面板；其他 native component/subsystem 仍 nominal-only。
- 事件类型按非空 catalog 动态收敛，只显示 fault/degradation，不显示 constraint。
- 新增 schema allowlist 单测与真实 Chromium 事件 Run：选择 torque authority loss，配置 10–20 s / 0.25，验证 injection evidence、有效力矩比例和 Run Bundle SHA-256。
- 最终状态以 PR 同一 head 的 official Basilisk 2.11.0、Chromium 和共享回归为准。