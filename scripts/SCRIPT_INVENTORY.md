# SCRIPT_INVENTORY

版本：0.7.8

本清单是第一阶段模块自查和第二阶段交叉检查的结果。所有在用非生产脚本均位于根目录 `scripts/`；`src/` 只保留生产模块和正式 CLI，`tests/` 只保留自动化测试。

| 脚本 | 类别 | 状态 | 处理结论 |
|---|---|---|---|
| `audit_a4r_dc2_run_bundles.py` | A4R-DC2 evidence audit | active | Audits Composite Digital Twin Run Bundle adapter, legacy flags, manifests, and SHA-256 evidence |
| `audit_campaign_dataset.py` | Campaign 数据审计 | active | 校验数据集完整性、失败可见性和跨运行稳定输出 |
| `audit_interactive_baseline.py` | 交互扩展兼容基线 | active | 冻结测试计数、Doctor、release-check、OpenAPI 与脚本清单哈希 |
| `bootstrap_local.sh` | 环境初始化 | active | 保留并统一文件头与入口 |
| `bootstrap_windows.ps1` | 环境初始化 | active | 保留并统一文件头与入口 |
| `build_bsk_compat_wheels.py` | 依赖兼容构建 | active | 从已核验官方 BSK Wheel 仅重写依赖元数据并验证二进制载荷逐文件不变 |
| `build_dependency_security_gate.py` | 安全门禁 | active | 对完全未变化且不超过 72 小时的近期有效依赖审计执行可证明的限时继承，变化或过期时 fail-closed |
| `check_api_stack.ps1` | 环境诊断 | active | 保留并统一文件头与入口 |
| `check_basilisk_runtime.py` | Basilisk运行时检查 | active | 输出机器可读 Basilisk 安装、导入、版本与可用状态；正式数据门使用 fail-closed 结果。 |
| `check_engineering_verification_matrix.py` | 验证审计 | active | 保留并统一文件头与入口 |
| `check_experiment_center.py` | 功能审计 | active | 保留并统一文件头与入口 |
| `check_script_dependency_governance.py` | 工程治理 | active | 保留并统一文件头与入口 |
| `check_task_center.py` | 功能审计 | active | 保留并统一文件头与入口 |
| `check_workbench_reports_diagnostics.py` | 功能审计 | active | 保留并统一文件头与入口 |
| `clean_local_runtime_artifacts.py` | 本地洁净治理 | active | Ubuntu/Linux 入口；默认 dry-run，显式 `--apply` 后仅清理项目根目录内的缓存、运行目录、字节码及指定临时证据 |
| `diagnose_windows.ps1` | 环境诊断 | active | 保留并统一文件头与入口 |
| `export_astrograph_diagnostic_mapping.py` | 诊断映射导出 | active | 导出故障机理、遥测特征和诊断 Pipeline 的受控互操作包 |
| `generate_astrograph_rw_fault_dataset.py` | 数据集生成 | active | 生成反作用轮故障/名义配对数据和受控 sidecar 契约 |
| `generate_taskspec_schema.py` | 构建生成 | active | 保留并统一文件头与入口 |
| `generate_whole_spacecraft_adcs_eps_dataset.py` | 数据集生成 | active | 生成整星 ADCS/EPS 正常与单故障成对时序数据、清单和校准证据 |
| `ensure_basilisk_runtime.py` | Basilisk运行时引导 | active | 默认正式仿真前置：验证现有 bsk，或下载官方固定 Wheel、校验哈希、构建 metadata-only satfix1 后安装；不修改仿真二进制载荷。 |
| `fetch_official_bsk_and_build_offline_bundle.py` | 可选离线依赖构建 | active | 固定官方来源与哈希，生成 metadata-only satfix1；不作为默认运行时前置 |
| `install_offline_bsk.py` | 可选离线依赖安装 | active | 仅安装构建报告为 PASS 的平台匹配 Wheel |
| `install_windows.ps1` | 安装 | active | 保留并统一文件头与入口 |
| `repair_windows_environment.ps1` | 环境修复 | active | 保留并统一文件头与入口 |
| `review_astrograph_dataset_pair.py` | 数据治理 | active | 对技术门已通过的数据 Pair 记录具名审核 |
| `review_diagnostic_signature.py` | 诊断治理 | active | 冻结或拒绝精确诊断签名哈希 |
| `run_astrograph_basilisk_signal_gate.py` | 原生验收 | active | 执行 18 案例、9 配对 Basilisk 物理与诊断候选门 |
| `run_astrograph_roundtrip.py` | AstroGraph 验收 | active | 校验候选模型、知识图谱投影与融合闭环 |
| `run_basilisk_validation_suite.py` | 模型验证 | active | 保留并统一文件头与入口 |
| `run_capacity_benchmark.py` | 性能容量基准 | active | 以隔离子进程执行时长、采样、模型规模、并发和批量吞吐矩阵并生成正式容量证据 |
| `run_browser_e2e.py` | 浏览器测试 | active | 保留并统一文件头与入口 |
| `run_coupling_causality_suite.py` | 耦合因果验证 | active | 执行共享基线与单因素扰动的密封 Basilisk 配对试验并签发证据清单 |
| `run_coupling_completeness_gate.py` | 耦合治理 | active | 保留并统一文件头与入口 |
| `run_controlled_experience_acceptance.py` | 经验复用验收 | active | 以真实密封 Run Bundle 验证候选编译、评测、双人审批、快照、禁用、回滚、撤销、隔离和篡改拒绝 |
| `run_diagnostic_pipeline.py` | 诊断运行 | active | 执行受控诊断 Pipeline，并输出特征向量、候选证据图和交接记录 |
| `run_capability_integration_gate.py` | 能力一致性治理 | active | 校验基础模块映射与因果协议 |
| `run_engineering_verification_matrix.py` | 工程验证 | active | 保留并统一文件头与入口 |
| `run_experiment_browser_e2e.py` | 浏览器测试 | active | 保留并统一文件头与入口 |
| `run_experience_retrieval_benchmark.py` | 经验性能基准 | active | 测量 1k/10k/100k approved 经验索引检索 p50/p95、插入耗时和存储增长 |
| `run_experience_longitudinal_eval.py` | 经验纵向评测 | active | 执行真实 vLLM 无经验/批准经验配对 A/B、物理 Run Bundle 和 Token 符号检验 |
| `run_final_recovery_acceptance.py` | 恢复最终验收 | active | 串联六项能力严格遥测验收与逐文件全量回归 |
| `run_full_regression_supervised.py` | 全量回归监督 | active | 按文件隔离 pytest 并即时保存退出码和 JUnit |
| `run_interactive_performance_benchmark.py` | 交互软实时性能验收 | active | 实测 quantum、七档倍速、30 分钟漂移、指令/遥测延迟、并发 4、背压和 4 小时仿真长稳；CI 短测不外推正式 PASS |
| `run_interactive_assurance_acceptance.py` | 交互安全与可靠性验收 | active | 逐次执行认证、授权、注入、洪泛、篡改、竞态、恢复、确定性、资源释放和分段/批处理配对矩阵，保存 JUnit 与日志 |
| `run_reliability_acceptance.py` | 纵向可靠性验收 | active | 聚合纵向、容量与安全证据，并验证固定快照复现、经验隔离、vLLM/Worker 中断恢复和全对象确定性 |
| `run_six_internal_capability_acceptance.py` | 内部能力验收 | active | 六项 active/internal 能力逐项记录 Adapter、Legacy、运行时与遥测证据 |
| `run_segmented_runtime_feasibility.py` | 交互运行时验证 | active | 验证同一 Basilisk foundation 实例分段推进、增量采样和单段等价性 |
| `run_platform_acceptance.py` | 跨平台验收 | active | Ubuntu/Windows 通用入口，执行发布身份、严格 Doctor、严格 release-check、密封运行包及 vLLM 证据验收 |
| `run_vllm_acceptance.py` | Agent验收 | active | 对 OpenAI-compatible vLLM 执行三项真实模型 TaskSpec、导出脚本、密封 Run Bundle 与物理验证验收 |
| `run_vllm_evidence_benchmark.py` | 模型性能基线 | active | 聚合真实模型调用证据并核验 vLLM 在线状态、GPU、延迟、Token、错误和重试预算 |
| `run_vllm_nl_e2e_matrix.py` | Agent全矩阵验收 | active | 分批执行31对象三模式及中英文/别名变体的真实vLLM端到端矩阵 |
| `run_vllm_nl_rejection_matrix.py` | Agent拒绝安全矩阵 | active | 在vLLM服务在线时验证歧义、未知、越权、过度声明和提示注入均于模型调用前结构化拒绝 |
| `sat-experience` (project CLI) | 经验库治理 | active | 从密封 Run Bundle 捕获轨迹，并管理检索、校验、候选 lesson、评测、双人审批、快照、禁用、回滚、备份和恢复 |
| `run_reliability_soak.py` | 可靠性耐久 | active | 运行长生命周期 Worker 与周期回收的真实 TaskSpec 耐久矩阵，记录资源增长并支持断点续跑 |
| `run_rw_fault_scientific_closure.py` | RW 科学验证 | active | 执行配对数据可分性、规则基线和 ML 基准 |
| `run_task_center_browser_e2e.py` | 浏览器测试 | active | 保留并统一文件头与入口 |
| `run_ui_feature_playwright.py` | 浏览器测试 | active | 验证工作台关键 UI 交互、结果展示与导出流程 |
| `scan_dependencies.py` | 安全审计 | active | 保留并统一文件头与入口 |
| `start_local.sh` | 服务启动 | active | 保留并统一文件头与入口 |
| `start_local_windows.ps1` | 服务启动 | active | 保留并统一文件头与入口 |
| `start_windows.ps1` | 服务管理 | active | 保留并统一文件头与入口 |
| `status_windows.ps1` | 服务管理 | active | 保留并统一文件头与入口 |
| `stop_windows.ps1` | 服务管理 | active | 保留并统一文件头与入口 |
| `upgrade_windows.ps1` | 升级 | active | 保留并统一文件头与入口 |
| `validate_adcs_fault_injection_effects.py` | 故障注入验证 | active | 对每类 ADCS 故障生成成对样本并量化注入效应 |
| `validate_coupling_causality.py` | 耦合验证 | active | 基线—单因素扰动配对验证源变化、响应方向和时延 |
| `verify_dependency_remediation.py` | 安全修复核验 | active | 将历史联网漏洞基线与当前锁文件逐项比较，不替代新一轮联网扫描 |
| `verify_recovery_third_party.py` | 恢复依赖核验 | active | 默认校验强制 WMM/SPICE；Basilisk 采用运行时身份门禁，离线 Wheel 为可选增强 |
| `verify_source_package.py` | 发布审计 | active | 保留并统一文件头与入口 |
| `assess_rw_jam_readiness.py` | RW 准备度 | active | 基于合同、原生门和具名审批证据执行 fail-closed 准备度判定 |
| `audit_object_scenario_coverage.py` | 全对象覆盖治理 | active | 审计 31 个产品对象、候选场景、主能力、脚本导出与直接效果证据覆盖 |

## 交叉检查结论

- `tools/` 与 `scripts/` 的并列入口已合并，`tools/` 已删除。
- `start_local_windows.ps1` 为前台开发启动；`start_windows.ps1` 为后台运维启动，职责不同，不合并。
- `bootstrap_windows.ps1` 为源码开发环境初始化；`install_windows.ps1` 为正式安装入口，职责不同，不合并。
- 当前没有需保留的废弃脚本；`scripts/deprecated/` 仅保留治理说明。
- 新增脚本必须通过 `check_script_dependency_governance.py`。

## 第二阶段相似脚本交叉检查

| 脚本对 | 相似原因 | 是否合并 | 结论 |
|---|---|---:|---|
| `install_windows.ps1` / `repair_windows_environment.ps1` | 均调用 Windows 环境引导脚本 | 否 | 前者执行常规安装，后者强制重建损坏环境，操作语义和风险不同 |
| `run_browser_e2e.py` / `run_experiment_browser_e2e.py` | 共用 Playwright、TestClient 和静态资源装载逻辑 | 否 | 前者验证主工作台与诊断/日志，后者验证实验中心模板、参数扫描与对比，验收对象不同 |
| `start_local_windows.ps1` / `start_windows.ps1` | 均启动 Windows 服务 | 否 | 前者为前台开发启动，后者为后台运维启动 |
| `bootstrap_windows.ps1` / `install_windows.ps1` | 均涉及安装环境 | 否 | 前者是底层环境引导，后者是面向交付用户的正式安装入口 |

未发现内容完全重复的脚本；`scripts/deprecated/` 无可执行脚本，不存在已知僵尸代码。
