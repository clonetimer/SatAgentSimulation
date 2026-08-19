# 卫星仿真 Agent 平台

基于 Basilisk 的受约束卫星仿真与数据生产平台。Agent 只生成或修改 Canonical TaskSpec；Capability Registry、Validator、Execution Planner、注册 Runner 和密封 Run Bundle 构成权威执行链。

## 当前基线

| 项 | 当前值 |
| --- | --- |
| 版本 | `0.7.8` |
| 发布状态 | `INTERNAL_ENGINEERING_BASELINE_READY` |
| 目标平台 | Ubuntu/Linux |
| Python | `>=3.10,<3.14` |
| 当前环境 | `/mnt/PRO6000_disk/Timer/Sat/.venv` |
| 管理工具 | `uv` |
| 模型服务 | 外部 vLLM，`http://127.0.0.1:8000/v1` |
| 仿真内核 | `bsk==2.11.0+satfix1` |

当前 RW_JAM 结果已批准为内部工程仿真基线，可用于受控训练和评测。该结论不代表真实反作用轮硬件、硬件标定、地面试验相关性、飞行验证或飞行鉴定。

## 核心链路

```text
自然语言 / 表单 / YAML / 模板
  -> Canonical TaskSpec
  -> Simulation DAG / Campaign Plan
  -> Validator / Agent Guard / Coupling Guard
  -> Registered Capability Runner
  -> Sealed Run Bundle
  -> Dataset / Report / Evidence
```

平台不允许 Agent 绕过注册表执行任意 Python，也不允许用模板或代理结果冒充真实模型调用或 Basilisk 原生证据。

可选的软实时交互模块默认关闭；启用后，首批整星、ADCS、EPS、Comm/Data 主能力支持同一实例的暂停、继续、单步、七档倍速、受控遥控和实时遥测。它不属于硬实时、硬件在环或飞行验证，操作与复验方法见[软实时交互仿真指南](docs/guides_zh/11_软实时交互仿真指南.md)。

当前推荐主线 Capability：

- `whole_spacecraft.unified_native.v1`
- `subsystem.adcs_unified_native.v1`
- `subsystem.eps.unified_native.v1`
- `subsystem.comm_data.unified_native.v1`
- `subsystem.propulsion.unified_native.v1`

## 安装

```bash
cd /mnt/PRO6000_disk/Timer/Sat/sat_agent_platform
UV_CACHE_DIR=/mnt/PRO6000_disk/Timer/Sat/.uv-cache \
uv pip install --python /mnt/PRO6000_disk/Timer/Sat/.venv/bin/python \
  --no-deps -e .

# 复核整包已冻结依赖；定制 bsk+satfix1 由整包环境提供
uv pip check --python /mnt/PRO6000_disk/Timer/Sat/.venv/bin/python
```

项目环境与 vLLM 服务端环境必须隔离。平台只通过 OpenAI-compatible HTTP 接口调用外部 vLLM，不在项目环境中安装 vLLM 服务端包。

## 常用入口

```bash
# 严格环境诊断
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python -m sat_sim.agent_cli \
  doctor --require-api --strict-assets

# 全量测试
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python -m pytest -q

# 严格发布检查
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python -m sat_sim.agent_cli \
  release-check --source-root . --strict-assets --output-dir reports/release_check

# Ubuntu/跨平台核心验收
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python scripts/run_platform_acceptance.py

# vLLM 三项代表性案例
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python scripts/run_vllm_acceptance.py \
  --base-url http://127.0.0.1:8000/v1 \
  --output-dir reports/vllm_acceptance

# 交互模块 CI 性能与安全/可靠性专项（短测不关闭正式长门）
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python \
  scripts/run_interactive_performance_benchmark.py --profile ci
/mnt/PRO6000_disk/Timer/Sat/.venv/bin/python \
  scripts/run_interactive_assurance_acceptance.py --matrix all
```

## RW 工程仿真基线

冻结执行器合同：

- 转动惯量 `J=0.015 kg*m^2`
- 力矩常数 `Kt=0.02 N*m/A`
- 电流限制 `I_limit=10 A`
- 适用范围 `simulation_only`

当前证据：

- Basilisk 原生案例 `18/18 PASS`
- 物理配对 `9/9 PASS`
- 诊断候选配对 `9/9 PASS`
- 诊断签名冻结 `3/3`
- 数据 Pair 具名批准 `9/9`
- readiness `READY_FOR_ASTROGRAPH_PROMOTION`

批准只覆盖精确冻结的合同、签名哈希和九个受审 Pair。新参数、新签名、新数据或新硬件主张必须重新执行技术门和人工治理。

## 当前证据

正式证据入口位于 `reports/`：

- `environment_doctor.json`
- `dependency_audit.json`
- `script_governance.json`
- `release_check/`
- `platform_acceptance/`
- `vllm_acceptance/`
- `rw_native_validation_report.json`
- `rw_jam_readiness.json`
- `rw_a_level_approval_authorization.json`
- `final_acceptance_report.md`

Windows 不属于当前 Ubuntu 部署目标，状态为 `N/A`；Linux 证据不得推断为 Windows PASS。

## 文档

- [当前文档导航](docs/guides_zh/00_文档导航.md)
- [Agent 能力边界](docs/Agent能力边界说明.md)
- [TaskSpec 规范](docs/TaskSpec规范.md)
- [安全策略](SECURITY.md)
- [验收清单](checklist.md)
- [规格](spec.md)
- [执行任务](task.md)
- [最终复验报告](reports/final_acceptance_report.md)

历史报告和旧版本指南已移出源码树，不作为当前行为、可信等级或发布状态的依据。
