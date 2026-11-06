# R83m 原生反作用轮实验数据资格审查

> 审查日期：2026-11-06  
> 审查性质：原始物理实验归档独立复核与数据集适用性审查  
> 结论：**物理实验归档可信度通过；可作为物理证据与数据集构建的 QA 来源；目前不具备最终遥测故障诊断训练/测试集资格。**

## 1. 原始来源与完整性

来源为 [SatAgentSimulation PR #88（R83m）](https://github.com/clonetimer/SatAgentSimulation/pull/88) 的原始 Basilisk 实验：

- [原生实验 Actions #40160788578](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40160788578)
- [原始归档 artifact #12586915899](https://github.com/clonetimer/SatAgentSimulation/actions/runs/40160788578/artifacts/12586915899)
- 实验执行引擎：`GENUINE_BASILISK_REACTIONWHEELSTATEEFFECTOR_AND_FSW`

本轮实际下载归档并重新计算 SHA-256，与 PR 归档记录一致：

| 文件 | 大小 | SHA-256 |
|---|---:|---|
| 原始 ZIP | 271,722 bytes | `db83c8b50a833d2f9c5074cb3f6f9d44c8e0109a2a7be4ddcec5f70bff754796` |
| `r83m_genuine_native_54_original.json` | 2,020,615 bytes | `635fd4e4cd36da39a15ba1289003ba84aaf4551b02de70c3d063ada878209502` |
| `compat_payload_review.json` | 847 bytes | `3f279f92c4e424ce0625083a190dda1b9ab183727fb789eb976abded67b9cdb9` |

兼容性审查记录为 Basilisk 2.11.0 的 metadata-only `satfix1` 发行兼容修正，原生载荷 880 项、字节数保持不变。它不代表物理内核被替换为 Python 代理。

## 2. 数据规模与实验结构

原始 JSON 的机器可读摘要：

| 项目 | 数量/状态 |
|---|---:|
| 原生物理案例 | 54 |
| 正常案例 | 27 |
| 故障案例 | 27 |
| 故障—正常配对 | 27 |
| 轮系与初始状态独立条件组 | 9 |
| 每个案例遥测/状态采样行 | 31 |
| 总采样行 | 1,674 |
| 采样时间 | 0–6 s |
| 采样间隔 | 0.2 s |
| 训练模型 | 未训练 |
| 独立 VAL/TEST/FINAL | 未生成 |
| Stable/飞行资格 | 不具备 |

每个独立条件组包含 3 种参考激励模式（0°、0.5°、0.9°），并分别执行正常与 75% 力矩权限损失条件。故 27 组配对并不等于 27 个完全独立的物理条件；统计推断应以 9 个轮系/初始状态组为主要分组单位。

故障权限由原生 C++ 反作用轮状态效应器读取确认：名义上限 0.020 N·m，故障后上限 0.005 N·m。实际应用力矩由 `rwOutMsgs` 读回核验。

## 3. 物理可观测性结果

原始 `native_physical_paired_outcomes` 共 27 个配对结果。已使用独立只读审计程序逐行复算，包含正常/故障配对、原生 C++ 权限读回、0.2 s 时间网格、封存 SHA、非 QA 通道以及故障后实际施加力矩约束。故障在 2.4 s 原生调度暂停点修改权限，降额后的执行效果从下一个 2.6 s 样本起核验；2.4 s 时刻的旧力矩样本不应误判为超限。

| 参考激励 | 有实际轮速差异的配对 | 结论 |
|---|---:|---|
| 0° | 0/9 | 当前条件下未观察到物理轮速差异 |
| 0.5° | 0/9 | 当前条件下未观察到物理轮速差异 |
| 0.9° | 9/9 | 9 个条件组均观察到物理轮速差异 |

0.9° 条件下，故障与正常组的最大故障后轮速差异：

- 最小值：0.00289476886 rad/s
- 中位数：0.00520032949 rad/s
- 最大值：0.01059166308 rad/s

实际应用力矩差异也为 9/9 可观测，范围约 0.00099860832–0.00245712156 N·m。

对全部 9 个轮系/初始状态组逐列复算后，0° 和 0.5° 下本归档所记录的非 QA 状态/命令通道在正常与故障配对间均无数值差异；0.9° 下差异出现在 FSW 机体力矩请求、机体状态、轮分配请求和轮速等通道。

**物理解释边界：**这是特定 Basilisk 模型、FSW、参考调度、初始状态和故障权限条件下的实验结果。0.9° 不是通用检测阈值，也不是经过真实卫星或硬件标定的安全要求。

## 4. 数据集特征资格与防泄漏规则

本归档是原生仿真状态/控制信号，不等同于带传感器噪声的真实遥测。

| 字段组 | 当前含义 | 数据集处理建议 |
|---|---|---|
| `rw.native_speed_rad_s_*` | 原生反作用轮转速 | 候选可观测信号；后续仍需明确测量链与噪声模型 |
| `hub.omega_bn_b_rad_s_*` | 仿真机体角速度状态 | 标记为 simulator truth；不能直接宣称为陀螺仪测量 |
| `hub.sigma_bn_*` | 仿真姿态状态 | 标记为 simulator truth；不能直接宣称为星敏感器测量 |
| `fsw.body_request_nm_*` | FSW 机体力矩请求 | 控制器内部/命令类信号；只有确认实际遥测可用后才进入部署特征 |
| `rw.native_allocator_request_nm_*` | 轮分配请求 | 控制器内部/命令类信号；需确认实际遥测接口 |
| `fsw.native_reference_mrp_*` | FSW 参考姿态 | 任务/制导上下文；需确认实际可访问性 |
| `rw.native_target_applied_torque_nm_QA_ONLY` | 目标轮实际施加力矩 QA 读回 | **禁止进入模型输入特征**；仅用于物理验证 |
| `*_QA_ONLY` 元数据 | 故障轮、故障机理、故障时刻、权限真值等 | 仅作为离线标签/审计元数据，禁止进入模型输入 |

此外，原始归档明确标注 `fault_truth_only_for_offline_physics_QA=true`、`model_inference_uses_truth=false`。后续转换器必须将特征矩阵与标签/QA 元数据分离，不能把故障轮索引、真实权限上限、实际施加力矩或故障起始时间混入模型输入。

## 5. 对故障诊断数据集的正确使用方式

建议为每个样本保留两条独立语义：

1. **物理真值（ground truth）**：是否实际注入 75% 权限损失、故障轮索引、故障起始时刻、C++ 权限读回。
2. **可观测性（observability）**：在给定激励与当前观测通道下，是否出现可区分的物理响应。

特别注意：0° 和 0.5° 下的 75% 故障案例不能被重新标成“正常”。它们是**故障真实存在但当前激励/观测条件下不可辨识**的案例。若将它们简单标为正常，会制造标签冲突；若强迫模型在无可观测信息时检出故障，则会夸大诊断能力。

推荐的标签结构：

- `fault_truth`：`healthy` / `rw_torque_authority_loss_75pct`
- `observability_status`：`observable_under_tested_excitation` / `not_observable_under_tested_excitation`
- `excitation_mode`：`zero` / `pulse05` / `pulse09`
- `independent_condition_group`：9 个轮系/初始状态分组 ID
- `source_artifact_sha256`、`source_run_id`、`case_id`：来源追溯

这些标签与 QA 元数据应独立于模型特征存储。

## 6. 当前不具备最终训练集资格的原因

1. 仅 9 个轮系/初始状态条件组，且 54 个案例是成对、重复激励条件，不构成大规模独立样本。
2. 当前没有 IMU/陀螺仪和星敏感器的实际测量输出，也没有传感器噪声、偏置、漂移和采样异步。
3. `hub` 信号是仿真状态真值，不是传感器读数。
4. 尚未完成 nominal-only 误报率、噪声敏感性、不同 FSW 模式和独立初始条件验证。
5. 原始实验明确没有训练模型，也没有冻结 VAL/TEST/FINAL。

因此本归档应定位为**可信物理先导实验/QA 参考数据**，不能单独作为论文最终训练集、独立测试集或通用诊断基准。

## 7. 下一步实施顺序

建议以 R84 独立研究分支推进，不修改原始 R83m 归档：

1. 在同一真实 Basilisk FSW—RW 物理链上接入原生 IMU/陀螺仪观测和轮速遥测；如任务链要求，再加入星敏感器姿态观测。
2. 预注册传感器噪声、偏置、采样频率、异步和随机种子；健康组必须同步执行，统计误报。
3. 保留 0°/0.5°/0.9° 原始激励对照，增加独立轮系、初始状态和激励组；不得因不利结果而删除不可观测案例。
4. 形成严格的特征白名单、QA 字段黑名单、标签 schema 和 provenance manifest。
5. 按独立物理条件组划分训练/验证/测试；禁止把同一条件组的时间窗随机拆到不同集合。
6. 先完成数据专家审查和独立测试，再决定是否训练诊断模型。

## 8. 可复现的原始归档验收

本次新增只读工具 [`scripts/audit_r83m_native_archive.py`](../../scripts/audit_r83m_native_archive.py)、防篡改单测 [`tests/test_r83m_native_archive_audit.py`](../../tests/test_r83m_native_archive_audit.py)，以及 [机器可读审计报告](r83m_original_archive_audit.json)。无需重新仿真，不会修改原始 ZIP，不生成可以冒充诊断遥测的训练样本。

```bash
# 将 GitHub Actions #40160788578 的原始 ZIP 下载到本机后：
python -m pytest -q tests/test_r83m_native_archive_audit.py
python scripts/audit_r83m_native_archive.py ./r83m-original.zip --report ./r83m-original-audit.json
```

审计硬性核对：原始 ZIP/两个成员文件 SHA-256、固定 54 案例与 27 配对、9 条轮系与初始状态条件、31 点 × 0.2 s 采样、故障轮与注入时间、原生 C++ `u_max` 读回、故障后 `u_current` 限幅、已归档正常—故障逐行差异与摘要值。若任何签名或原始数值不符，则 `ok=false` 并返回非零退出码。

**区分两种测试证据：**无原始 ZIP 的 CI 运行是合成夹具的验证器回归，不代表执行过真实 Basilisk；只有通过哈希绑定的 `audit_archive` 才能把 `physics_archive_verified` 设为 `true`。如需 GitHub 端再次复验，可以手动触发 [`r83m-original-archive-audit.yml`](../../.github/workflows/r83m-original-archive-audit.yml) 并启用 `verify_original_archive`，让 Actions 从原始归档获取数据并输出独立 JSON 收据。该 GitHub Actions 原始 artifact 的当前保留截止时间为 **2026-11-15 04:09 UTC**；截止后需用已保存的原始字节副本复验，不得以重跑数据替代原始样本。

**当前决定：不训练、不生成虚假的 VAL/TEST 资格、不合并 R83j–R83m 实验草稿为生产能力。** 先将本归档固定为可追溯的物理证据，再补足传感器观测链。
