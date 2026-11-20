# 归档索引与待审清单（2026-11）

**状态：仅提出建议，尚未执行文件迁移。** 核查基线为 `3bdb3b96ce63f5c5bd197ee3f9a03869f87846cb`。本轮实际改动只涉及 README、新文档地图、本清单和新增验收历史；既有文件均在原路径。

## 已执行的目录迁移

暂无。下表所有目标均为**拟定位置**，不能当作已存在的新入口；历史文档与证据继续从原路径访问。

## 范围与口径

拟归档路径统一为 `docs/archive/2026-11/<完整原仓库相对路径>`，保留目录层次以避免重名。目录行覆盖其中全部已跟踪文件；通配行明确限定根层。数目来自基线的 Git 文件清单，目录数不与文件数相加。

| 本次核查范围 | 已跟踪文件 | 建议归档候选 | 保留或暂缓 |
|---|---:|---:|---:|
| `reports/progress_checkpoint/` | 564 | 489 | 75 |
| P9 文档及两份单次报告 | 63 | 20 | 43 |
| 四份 R84 文档及 `reports/native_rw_cluster/` | 18 | 12 | 6 |
| 合计（仅上述范围） | 645 | 521 | 124 |

“建议归档”表示历史版本已经结束、冻结或由后继记录接续，**不等于原实验通过、失败原因查清或整个平台已完成**。原 FAIL、UNPROVEN、未执行、待验快照和物理限制均保留；旧进度入口的 ACTIVE / UNPROVEN 也不在本轮改写。当前能力与资格请沿[文档地图](DOC_MAP.md)核对具体模型、运行证据和发布声明。

## 一、progress_checkpoint：建议归档

以下八个目录共 418 个文件。收尾关系可从各自 README、[R30 历史入口](../reports/progress_checkpoint/r30-integration/CURRENT.md)、[维护收尾记录](../reports/progress_checkpoint/maintenance-closure-2026-11-02/README.md)及[当时的需求验收状态](../reports/current_requirements_status.md)核对。

| 原路径 | 拟归档路径 | 文件数 | 这是什么／为什么建议归档 |
|---|---|---:|---|
| [reports/progress_checkpoint/r14-event-guard-candidate/](../reports/progress_checkpoint/r14-event-guard-candidate/) | `docs/archive/2026-11/reports/progress_checkpoint/r14-event-guard-candidate/` | 24 | 已结束的 AI 事件失败候选；真实复验失败、纠错超时等终态保留。后续 R19、最终集成与维护验收接续，归档不表示候选通过。 |
| [reports/progress_checkpoint/r19-event-intent-candidate/](../reports/progress_checkpoint/r19-event-intent-candidate/) | `docs/archive/2026-11/reports/progress_checkpoint/r19-event-intent-candidate/` | 68 | 已结束的事件语义候选；创建成功与修改失败分别保留，不能用协议检查替代生成流程验收。由 R30 等后续阶段接续。 |
| [reports/progress_checkpoint/r30-integration/](../reports/progress_checkpoint/r30-integration/) | `docs/archive/2026-11/reports/progress_checkpoint/r30-integration/` | 131 | R30—R34 的源码覆盖层、安装和回归快照；CURRENT.md 已明确标作历史并指向 R43—R45。冻结源码后像和原始哈希一并保留。 |
| [reports/progress_checkpoint/r43-integration/](../reports/progress_checkpoint/r43-integration/) | `docs/archive/2026-11/reports/progress_checkpoint/r43-integration/` | 117 | R43／R45 的源码、布局和网页实验；由 R46 与维护收尾接续，保留 R43 原失败与 R45 后续结果的版本边界。 |
| [reports/progress_checkpoint/r46-interaction-ownership/](../reports/progress_checkpoint/r46-interaction-ownership/) | `docs/archive/2026-11/reports/progress_checkpoint/r46-interaction-ownership/` | 30 | 生成重入与过时下载反馈修复阶段；旧未验状态由原 69 例终态报告及耐久复核接续，history/ 和旧状态原文一起保存。 |
| [reports/progress_checkpoint/r46-original69-result/](../reports/progress_checkpoint/r46-original69-result/) | `docs/archive/2026-11/reports/progress_checkpoint/r46-original69-result/` | 2 | 已结束的 69 例真实调用及失败分类；严格 0/69 与未执行脚本记录不改写，后续 15 例不能替代这一批结果。 |
| [reports/progress_checkpoint/r48-compact-toolbar/](../reports/progress_checkpoint/r48-compact-toolbar/) | `docs/archive/2026-11/reports/progress_checkpoint/r48-compact-toolbar/` | 16 | 工具栏与交互的一次性验收；由最终集成及维护收尾接续，保留截图、源码覆盖层与当时未验范围。 |
| [reports/progress_checkpoint/final-integration/](../reports/progress_checkpoint/final-integration/) | `docs/archive/2026-11/reports/progress_checkpoint/final-integration/` | 30 | 固定版本的集成、模型调用与安装证据；当前需求状态已明确称其为历史，后续耐久与维护收尾另有报告。 |

以下四组只匹配 `progress_checkpoint/` 根层文件，共 71 个文件；不递归匹配同名目录。

| 原路径或根层匹配式 | 拟归档路径或匹配式 | 文件数 | 这是什么／为什么建议归档 |
|---|---|---:|---|
| `reports/progress_checkpoint/r11-*` | `docs/archive/2026-11/reports/progress_checkpoint/r11-*` | 61 | R11 的资格、AI、图表、时序与旧耐久观察。旧进程已不存在，没有退出码或终态报告，原因未定；仅冻结历史实例，不代表调查或资格已收口。停止观测情况见[原记录](../reports/progress_checkpoint/r11-endurance-stop-observation.json)。 |
| `reports/progress_checkpoint/v9-*` | `docs/archive/2026-11/reports/progress_checkpoint/v9-*` | 8 | V9 固定版本快照；原 FAILED_REQUIRES_REVIEW 状态保留，不用后继版本验收回填。 终态见[原记录](../reports/progress_checkpoint/v9-production-qualification__state.json)。 |
| [reports/progress_checkpoint/hardware-bounded-r11-independent-review.json](../reports/progress_checkpoint/hardware-bounded-r11-independent-review.json) | `docs/archive/2026-11/reports/progress_checkpoint/hardware-bounded-r11-independent-review.json` | 1 | 固定 R11 应用的独立审查收据，不授予真实硬件、飞行或新的耐久资格；与 R11 历史组配套。 |
| [reports/progress_checkpoint/evidence_manifest.json](../reports/progress_checkpoint/evidence_manifest.json) | `docs/archive/2026-11/reports/progress_checkpoint/evidence_manifest.json` | 1 | V9 八份快照的路径、大小与 SHA 索引，整体 UNPROVEN 原文保留；必须与被索引文件成组归档。 |

### 保留原位的 75 个文件

| 路径 | 文件数 | 处理理由 |
|---|---:|---|
| [maintenance-closure-2026-11-02/](../reports/progress_checkpoint/maintenance-closure-2026-11-02/) | 38 | 仍是历史功能验收的引用基准，含独立校验器、原失败、源码身份和安装态证据；不替代当前发布资格。 |
| [manual-endurance-review-2026-11-01/](../reports/progress_checkpoint/manual-endurance-review-2026-11-01/) | 13 | 原 24 小时实例的复核事实基准；自动不合格、漂移和物理警告仍保留，也不自动覆盖新源码。 |
| [model-distribution-2026-11-02/](../reports/progress_checkpoint/model-distribution-2026-11-02/) | 23 | 现行模型部署指南仍引用的公开下载、哈希和真实生成证据。 |
| [progress_checkpoint/README.md](../reports/progress_checkpoint/README.md) | 1 | 暂留历史导航，其旧阶段状态不改写；通过本索引区分历史记录与当前入口。 |

迁移依赖：上述目录及根层 JSON/XML/Python 文件受 `.gitattributes` 的原始字节、换行保护约束；改路径后旧规则将不再覆盖。文档链接、原清单位置、历史覆盖层脚本的路径假设也需接续。因此本表是候选，不能直接按目录批量移动。

## 二、R84：建议归档

以下十二个文件分别列出，报告和机器摘要应成组保留。R1／R2 的物理成立与诊断分离不足是不同结论，归档不改变原结论。

| 原路径 | 拟归档路径 | 这是什么／为什么建议归档 |
|---|---|---|
| [docs/R84R3_NATIVE_MULTICHANNEL_TELEMETRY_PILOT_20261106.md](R84R3_NATIVE_MULTICHANNEL_TELEMETRY_PILOT_20261106.md) | `docs/archive/2026-11/docs/R84R3_NATIVE_MULTICHANNEL_TELEMETRY_PILOT_20261106.md` | R3A 预注册已追加执行终态；只验证多通道来源与一致性，不代表编码器标定或诊断性能。 |
| [reports/native_rw_cluster/r84_native_imu_execution_20261106.md](../reports/native_rw_cluster/r84_native_imu_execution_20261106.md) | `docs/archive/2026-11/reports/native_rw_cluster/r84_native_imu_execution_20261106.md` | R0 的一次性原生 IMU 研究执行报告；与摘要配套，研究资格不外推。 |
| [reports/native_rw_cluster/r84_native_imu_execution_summary_20261106.json](../reports/native_rw_cluster/r84_native_imu_execution_summary_20261106.json) | `docs/archive/2026-11/reports/native_rw_cluster/r84_native_imu_execution_summary_20261106.json` | R0 机器收据；保留 PASS_RESEARCH_ONLY 及不具训练、正式测试资格的原字段。 |
| [reports/native_rw_cluster/r84r1_independent_noise_execution_20261106.md](../reports/native_rw_cluster/r84r1_independent_noise_execution_20261106.md) | `docs/archive/2026-11/reports/native_rw_cluster/r84r1_independent_noise_execution_20261106.md` | R1 独立噪声实验已结束；物理成立与单变量分离弱分别保留。 |
| [reports/native_rw_cluster/r84r1_independent_noise_summary_20261106.json](../reports/native_rw_cluster/r84r1_independent_noise_summary_20261106.json) | `docs/archive/2026-11/reports/native_rw_cluster/r84r1_independent_noise_summary_20261106.json` | R1 摘要；弱单特征分离不能改写为可靠检测，与对应报告配套。 |
| [reports/native_rw_cluster/r84r1_feature_exploration_for_r84r2_20261106.md](../reports/native_rw_cluster/r84r1_feature_exploration_for_r84r2_20261106.md) | `docs/archive/2026-11/reports/native_rw_cluster/r84r1_feature_exploration_for_r84r2_20261106.md` | R1 数据的事后特征探索；保留探索性定位和负结果，不作确认性检验。 |
| [reports/native_rw_cluster/r84r1_feature_exploration_summary_20261106.json](../reports/native_rw_cluster/r84r1_feature_exploration_summary_20261106.json) | `docs/archive/2026-11/reports/native_rw_cluster/r84r1_feature_exploration_summary_20261106.json` | 上述事后探索的机器摘要，与报告配套归档。 |
| [reports/native_rw_cluster/r84r2_e0_raw_imu_feature_screen_20261106.md](../reports/native_rw_cluster/r84r2_e0_raw_imu_feature_screen_20261106.md) | `docs/archive/2026-11/reports/native_rw_cluster/r84r2_e0_raw_imu_feature_screen_20261106.md` | R2-E0 原始 IMU 回溯筛查，非新增独立原生实验；保留回溯和负结果边界。 |
| [reports/native_rw_cluster/r84r2_multipulse_multifeature_execution_20261106.md](../reports/native_rw_cluster/r84r2_multipulse_multifeature_execution_20261106.md) | `docs/archive/2026-11/reports/native_rw_cluster/r84r2_multipulse_multifeature_execution_20261106.md` | R2 多脉冲多特征实验已结束；条件可观测与仍不能可靠区分的工况都保留。 |
| [reports/native_rw_cluster/r84r2_multipulse_multifeature_summary_20261106.json](../reports/native_rw_cluster/r84r2_multipulse_multifeature_summary_20261106.json) | `docs/archive/2026-11/reports/native_rw_cluster/r84r2_multipulse_multifeature_summary_20261106.json` | R2 条件可观测摘要，与报告配套；不授予正式误报率资格。 |
| [reports/native_rw_cluster/r84r3_multichannel_telemetry_pilot_execution_20261106.md](../reports/native_rw_cluster/r84r3_multichannel_telemetry_pilot_execution_20261106.md) | `docs/archive/2026-11/reports/native_rw_cluster/r84r3_multichannel_telemetry_pilot_execution_20261106.md` | R3A 多通道来源、时钟和物理一致性试验已结束；轮速仍是理想状态反馈上界。 |
| [reports/native_rw_cluster/r84r3_multichannel_telemetry_pilot_summary_20261106.json](../reports/native_rw_cluster/r84r3_multichannel_telemetry_pilot_summary_20261106.json) | `docs/archive/2026-11/reports/native_rw_cluster/r84r3_multichannel_telemetry_pilot_summary_20261106.json` | R3 来源核验摘要，与预注册和执行报告配套归档。 |

### 保留原位的六个文件

| 路径 | 处理理由 |
|---|---|
| [R84 原生 IMU 预注册](R84_NATIVE_IMU_RW_OBSERVABILITY_PREREG_20261106.md) | 活动工作流 `r84-native-imu-prereg.yml` 直接引用其路径。 |
| [R84-R1 独立噪声预注册](R84R1_INDEPENDENT_NOISE_OBSERVABILITY_PREREG_20261106.md) | `r84r1-independent-noise.yml` 直接引用；冻结页首与后续已执行结果分开阅读。 |
| [R84-R2 多脉冲预注册](R84R2_MULTIPULSE_MULTIFEATURE_OBSERVABILITY_PREREG_20261106.md) | `r84r2-multipulse-multifeature.yml` 直接引用；不改写早期未执行状态。 |
| [R83M 数据资格审查](../reports/native_rw_cluster/r83m_data_eligibility_review_20261106.md) | 仍是源物理基准说明，且命中活动工作流 `r83m-original-archive-audit.yml` 的路径条件。 |
| [R83M 原始归档审计](../reports/native_rw_cluster/r83m_original_archive_audit.json) | 同属当前源基准与活动工作流路径合同。 |
| [physics_ab.json](../reports/native_rw_cluster/physics_ab.json) | `tests/test_native_rw_cluster.py` 和 `native-rw-component-probe.yml` 使用此输出位置；不能归档整个父目录。 |

R84 的冻结配置、实现、脚本与测试仍是活动复验入口，不属于这份文档归档候选。

## 三、P9：建议归档

以下二十个文件包括十五份已交付子阶段计划、三份配套设计和两份单次报告。旧计划中的未勾选项保持原样，收口依据在理由栏给出；可继续核对[单机能力清单](engineering/p9-n-component-capability-inventory.md)、[O2 收尾](development-experience/p9-o2-a-eps-comm-data-event-semantics.md)和[O3 最终收尾节](development-experience/p9-o3-whole-spacecraft-event-evidence.md)。

| 原路径 | 拟归档路径 | 这是什么／为什么建议归档 |
|---|---|---|
| [docs/superpowers/plans/2026-11-13-p9a-independent-evaluation.md](superpowers/plans/2026-11-13-p9a-independent-evaluation.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-13-p9a-independent-evaluation.md` | P9-A 只读后处理与评估设计；后继 P9-B 已交付，主干 068d41bb／28e13252 留有接续。 |
| [docs/superpowers/plans/2026-11-13-p9a-object-tree-quantity-dictionary.md](superpowers/plans/2026-11-13-p9a-object-tree-quantity-dictionary.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-13-p9a-object-tree-quantity-dictionary.md` | 对象树和参量字典实施计划已交付；现行单机命名指南保留原位。 |
| [docs/superpowers/plans/2026-11-14-p9h-native-attitude-globe.md](superpowers/plans/2026-11-14-p9h-native-attitude-globe.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-14-p9h-native-attitude-globe.md` | 姿态真值、旋转及浏览器阶段计划；主干 6bf0ff7f 已集成，后续真实场景测试继续核对。 |
| [docs/superpowers/plans/2026-11-14-p9h-physical-acceptance-review.md](superpowers/plans/2026-11-14-p9h-physical-acceptance-review.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-14-p9h-physical-acceptance-review.md` | P9-H 早期方向纠错与完成度估算；后续阶段已接续，避免旧缺口估算被当成当前状态。 |
| [docs/superpowers/plans/2026-11-16-p9l-digital-scene-task-creation.md](superpowers/plans/2026-11-16-p9l-digital-scene-task-creation.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-16-p9l-digital-scene-task-creation.md` | L1—L4 已分别集成；旧页首实施中属于阶段快照，不能据其否定现有任务创建和真实回放。 |
| [docs/superpowers/plans/2026-11-17-p9-n1-reaction-wheel-workbench.md](superpowers/plans/2026-11-17-p9-n1-reaction-wheel-workbench.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-17-p9-n1-reaction-wheel-workbench.md` | RW 首批单机入口计划，后续 N4-A—D 已完成并覆盖其历史重开和结果导出。 |
| [docs/superpowers/plans/2026-11-18-p9-n3-native-thruster.md](superpowers/plans/2026-11-18-p9-n3-native-thruster.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-18-p9-n3-native-thruster.md` | 已交付 PR #172／合并 2cd59c2f 的推进器阶段计划，保留原生与工程范围。 |
| [docs/superpowers/plans/2026-11-18-p9-n3-native-fuel-tank.md](superpowers/plans/2026-11-18-p9-n3-native-fuel-tank.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-18-p9-n3-native-fuel-tank.md` | 已交付 PR #173／合并 440b7ebb 的贮箱阶段计划，正文已有阶段收尾记录。 |
| [docs/superpowers/plans/2026-11-18-p9-n3-native-thermal-node.md](superpowers/plans/2026-11-18-p9-n3-native-thermal-node.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-18-p9-n3-native-thermal-node.md` | 已交付 PR #174／合并 40c34ce5；旧末尾待验勾选由后续 N4-C3 正式热节点旅程接续。 |
| [docs/superpowers/plans/2026-11-18-p9-n4-single-component-workbench.md](superpowers/plans/2026-11-18-p9-n4-single-component-workbench.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-18-p9-n4-single-component-workbench.md` | 正文已记录 A/B/C1/C2/C3/D 完成，九类单机执行、历史与完整包导出由 PR #181／#182 收尾。 |
| [docs/superpowers/plans/2026-11-19-p9-n5-adcs-subsystem-closed-loop.md](superpowers/plans/2026-11-19-p9-n5-adcs-subsystem-closed-loop.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-19-p9-n5-adcs-subsystem-closed-loop.md` | 正文已记录 N5-A—E 完成，PR #183／#184／#185 接续；能力边界在现行清单中保留。 |
| [docs/superpowers/plans/2026-11-19-p9-n5-e-guided-adcs-faults.md](superpowers/plans/2026-11-19-p9-n5-e-guided-adcs-faults.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-19-p9-n5-e-guided-adcs-faults.md` | PR #185 已交付受控 ADCS 事件旅程，归档阶段计划不扩大故障白名单。 |
| [docs/superpowers/plans/2026-11-19-p9-o1-native-subsystem-workbench.md](superpowers/plans/2026-11-19-p9-o1-native-subsystem-workbench.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-19-p9-o1-native-subsystem-workbench.md` | 主干 5f61e73a 已合入，后续 O2 收尾包括 EPS／通信正常运行；旧实施中状态保持原样。 |
| [docs/superpowers/plans/2026-11-19-p9-o2-eps-comm-data-effects-ab.md](superpowers/plans/2026-11-19-p9-o2-eps-comm-data-effects-ab.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-19-p9-o2-eps-comm-data-effects-ab.md` | 正文正式收尾与 PR #187／合并 7207244a 已记录事件对照和用户旅程。 |
| [docs/superpowers/plans/2026-11-19-p9-o3-whole-spacecraft-event-evidence.md](superpowers/plans/2026-11-19-p9-o3-whole-spacecraft-event-evidence.md) | `docs/archive/2026-11/docs/superpowers/plans/2026-11-19-p9-o3-whole-spacecraft-event-evidence.md` | 旧计划仍待验；开发经验的“O3-B/C/D 最终同头验收与 Artifact 独立复核”已记录 PR #190／合并 5ae1a700 的后继收口。 |
| [docs/superpowers/specs/2026-11-18-p9-n3-native-thruster-design.md](superpowers/specs/2026-11-18-p9-n3-native-thruster-design.md) | `docs/archive/2026-11/docs/superpowers/specs/2026-11-18-p9-n3-native-thruster-design.md` | PR #172 的一次性阶段设计，与对应计划共同归档。 |
| [docs/superpowers/specs/2026-11-18-p9-n3-native-fuel-tank-design.md](superpowers/specs/2026-11-18-p9-n3-native-fuel-tank-design.md) | `docs/archive/2026-11/docs/superpowers/specs/2026-11-18-p9-n3-native-fuel-tank-design.md` | PR #173 的一次性阶段设计，与对应计划共同归档。 |
| [docs/superpowers/specs/2026-11-18-p9-n3-native-thermal-node-design.md](superpowers/specs/2026-11-18-p9-n3-native-thermal-node-design.md) | `docs/archive/2026-11/docs/superpowers/specs/2026-11-18-p9-n3-native-thermal-node-design.md` | PR #174 的一次性阶段设计；热模型可行性文档仍引用，实际迁移前须接续该链接。 |
| [reports/p9a/evaluation-boundary-20261113.md](../reports/p9a/evaluation-boundary-20261113.md) | `docs/archive/2026-11/reports/p9a/evaluation-boundary-20261113.md` | P9-A 的单次边界审查；后继 P9-B 已交付，原本地 Basilisk 收集失败保留。 |
| [reports/p9c/lifecycle-hardening-20261113.md](../reports/p9c/lifecycle-hardening-20261113.md) | `docs/archive/2026-11/reports/p9c/lifecycle-hardening-20261113.md` | P9-C 的单次生命周期修复记录；受控 Node 结果与 GPU／原生验收边界保持原样。 |

### 保留或暂缓的 43 个文件

十二份计划仍被活动工作流的路径条件引用，继续保留原位：

| 现有计划（位于 docs/superpowers/plans/） | 数量 | 引用方／处理理由 |
|---|---:|---|
| [2026-11-14-p9e-object-run-results.md](superpowers/plans/2026-11-14-p9e-object-run-results.md) | 1 | p9e-object-run-results.yml。 |
| [2026-11-14-p9f-telemetry-driven-globe.md](superpowers/plans/2026-11-14-p9f-telemetry-driven-globe.md) | 1 | p9f-telemetry-globe.yml。 |
| [2026-11-14-p9g-default-entry-convergence.md](superpowers/plans/2026-11-14-p9g-default-entry-convergence.md) | 1 | p9g-navigation.yml。 |
| [2026-11-14-p9i-birds4-cad-reference-integration.md](superpowers/plans/2026-11-14-p9i-birds4-cad-reference-integration.md) | 1 | p9i-birds4-cad-import.yml。 |
| [2026-11-16-p9l4-three-mode-user-journeys.md](superpowers/plans/2026-11-16-p9l4-three-mode-user-journeys.md) | 1 | p9l4-three-mode-journeys.yml。 |
| [2026-11-17-p9m2-cad-engineering-candidates.md](superpowers/plans/2026-11-17-p9m2-cad-engineering-candidates.md) | 1 | p9m2-cad-candidates.yml。 |
| [2026-11-17-p9m3-evidence-review.md](superpowers/plans/2026-11-17-p9m3-evidence-review.md) | 1 | p9m3-installation-evidence.yml。 |
| [2026-11-17-p9m4-d-visual-geometry-review.md](superpowers/plans/2026-11-17-p9m4-d-visual-geometry-review.md) | 1 | p9m4-unified-mapping.yml；仅参考几何完成，不扩大为实装资格。 |
| [2026-11-17-p9m4-glb-node-crosswalk.md](superpowers/plans/2026-11-17-p9m4-glb-node-crosswalk.md) | 1 | p9m4-unified-mapping.yml。 |
| [2026-11-17-p9m4-real-evidence-registry.md](superpowers/plans/2026-11-17-p9m4-real-evidence-registry.md) | 1 | p9m4-unified-mapping.yml。 |
| [2026-11-17-p9m4-unified-asset-mapping.md](superpowers/plans/2026-11-17-p9m4-unified-asset-mapping.md) | 1 | p9m4-unified-mapping.yml。 |
| [2026-11-17-p9m4i-ai-engineering-asset-generation.md](superpowers/plans/2026-11-17-p9m4i-ai-engineering-asset-generation.md) | 1 | p9m4-unified-mapping.yml 与 p9m4i-synthetic-assets.yml。 |

其余三十一份为未收口事项、当前输入、指南或仍适用的开发经验：

| 现有路径或明确匹配式 | 文件数 | 处理与理由 |
|---|---:|---|
| [docs/superpowers/plans/2026-11-13-p9c-renderer-technical-decision.md](superpowers/plans/2026-11-13-p9c-renderer-technical-decision.md) | 1 | 保留：现行 Globe.gl／Three.js 技术决策，包含离线 CSP 选择依据。 |
| [docs/superpowers/plans/2026-11-14-p9i-evidence-backed-spatial-assets.md](superpowers/plans/2026-11-14-p9i-evidence-backed-spatial-assets.md) | 1 | 暂缓：真实资产、安装锚点与来源审定未完整闭合。 |
| [docs/superpowers/plans/2026-11-16-p9k-strict-telemetry-time-association.md](superpowers/plans/2026-11-16-p9k-strict-telemetry-time-association.md) | 1 | 暂缓归档判定：该计划末尾仍待最终重验收据；这只是证据接续需核准，不表示现有遥测功能未实现。 |
| [docs/superpowers/plans/2026-11-16-p9m-reference-cad-assembly-review.md](superpowers/plans/2026-11-16-p9m-reference-cad-assembly-review.md) | 1 | 暂缓：目标飞行单元、坐标基准和真实安装审查未核准。 |
| [docs/superpowers/plans/2026-11-17-p9-n-component-independent-simulation.md](superpowers/plans/2026-11-17-p9-n-component-independent-simulation.md) | 1 | 暂缓整体归档：总路线仍含 CSS 仅探针验证、输出合同等事项；只归档已收口子计划。 |
| [docs/superpowers/reviews/2026-11-17-p9m3-birds4-candidate-evidence-review.md](superpowers/reviews/2026-11-17-p9m3-birds4-candidate-evidence-review.md) | 1 | 暂缓：审查仍待处理，参考图不能替代基准点、左右手系和六自由度安装证据。 |
| [docs/superpowers/reviews/2026-11-17-p9m4i-three-device-source-intake.md](superpowers/reviews/2026-11-17-p9m4i-three-device-source-intake.md) | 1 | 暂缓并保留原位：用途签核和推理资格尚未完整获得，且活动 CI 直接引用。 |
| [docs/superpowers/benchmarks/2026-11-17-p9m4i-parametric-input.schema.json](superpowers/benchmarks/2026-11-17-p9m4i-parametric-input.schema.json) | 1 | 保留：活动校验输入与工作流路径合同，不是一次性报告。 |
| [docs/superpowers/benchmarks/2026-11-17-p9m4i-three-device-benchmark.json](superpowers/benchmarks/2026-11-17-p9m4i-three-device-benchmark.json) | 1 | 保留：活动测试和工作流实际读取的输入。 |
| `docs/development-experience/p9*.md` | 17 | 保留整组：可复用的物理、接口和验收经验，部分还追加 P10 更新；浏览器、O3、映射边界、GLB 审计四份被 CI 直接引用。 |
| [docs/development/p9-n1-ci-lessons-2026-11-17.md](development/p9-n1-ci-lessons-2026-11-17.md) | 1 | 保留：物理、浏览器与工作流证据分层经验仍适用。 |
| [docs/engineering/p9-n-component-capability-inventory.md](engineering/p9-n-component-capability-inventory.md) | 1 | 保留：当前原生、工程参考及仅探针能力边界入口。 |
| [docs/engineering/p9-n-thermal-native-feasibility.md](engineering/p9-n-thermal-native-feasibility.md) | 1 | 保留：官方热模型的单位、消息拓扑、常数和数值条件事实基准。 |
| [docs/guides_zh/P9A_单机参量命名规范.md](guides_zh/P9A_单机参量命名规范.md) | 1 | 保留：现行命名规范。 |
| [docs/guides_zh/P9C_卫星数字场景本地运行.md](guides_zh/P9C_卫星数字场景本地运行.md) | 1 | 保留原位：仍有本地资源说明且被工作流引用；其中仅演示数据的段落是旧阶段边界，当前真实来源见文档地图。 |

## 实际迁移前需处理的依赖

- 活动代码、测试、工作流的读取路径或触发条件：本轮列为保留项，后续若调整须另行确定范围。
- 文档跳转、目录内相对链接、原证据清单及覆盖层脚本：保持可追溯关系，不在原封存证据中直接重写历史身份。
- 原始字节与换行保护：迁移前后逐文件核验内容和哈希，并处理新路径的 `.gitattributes` 覆盖；本轮未修改该文件。
- 待实际迁移时，在本页登记已执行的“原路径 → 新路径 → 是什么／为什么归档”，与上面的候选表分开。

## 本轮整理经验

- README 只承担项目定位与首次运行入口；原文完整留存在验收历史中，并提供新的可点击链接，避免移动后旧相对路径失效。
- 新人需要显式准备三维资源，并能分辨演示画面与本次封存运行；`.sat_sim_api/` 含任务和队列状态，不能称作可随手删除的临时目录。
- 文件名带阶段编号不代表可以归档：有的预注册仍是 CI 路径合同，有的经验持续更新，有的研究记录保留着有效负结果。按用途与后继证据判断，不能按前缀一刀切。
