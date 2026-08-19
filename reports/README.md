# 当前验证证据

本目录只保留当前工程仿真基线的结构化证据。历史报告、临时结果和重复仿真副本不属于源码交付。

主要入口：

- `environment_doctor.json`：严格 Doctor。
- `dependency_audit.json`：新鲜依赖漏洞扫描。
- `script_governance.json`：脚本与依赖治理。
- `release_check/`：严格发布闭环。
- `platform_acceptance/`：Ubuntu 平台核心验收。
- `vllm_acceptance/`：真实 vLLM 三案例验收。
- `rw_native_validation_report.json`：RW 原生 18 案例门。
- `rw_jam_readiness.json`：RW_JAM 准备度。
- `rw_a_level_approval_authorization.json`：内部工程基线具名授权。
- `final_acceptance_report.md`：最终复验判定。

这些证据仅支持内部工程仿真、训练和评测，不支持真实硬件或飞行验证声明。
