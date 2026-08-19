# 六项 active/internal 能力本地 Basilisk 最终验收指南

## 1. 适用范围

本指南只验收以下六项能力：

```text
reference.public_satellite_case.v1
subsystem.adcs_basilisk_fsw.v1
subsystem.thermal_reduced_order.v1
whole_spacecraft.basilisk_6dof.v1
whole_spacecraft.orbit_attitude_thermal.v1
whole_spacecraft.bsksim_foundation.v1
```

本地运行不要求项目目录中存在离线 Basilisk Wheel 或 `satfix1` 构建报告。Basilisk 可由用户在当前 Python 环境中自行安装。

## 2. 接受的 Basilisk 运行时身份

严格运行时门禁接受：

```text
bsk==2.11.0
bsk==2.11.0+satfix1
```

其中 `2.11.0+satfix1` 仅为 metadata-only 兼容变体。接受某个版本只证明运行时身份和必需模块可导入，不代表飞行标定、高保真认证或物理精度认证。

## 3. 环境预检

在项目根目录执行：

```bash
python -c "import importlib.metadata as m; import Basilisk; print('bsk=', m.version('bsk')); print('Basilisk=', list(Basilisk.__path__))"
python scripts/verify_recovery_third_party.py --require-basilisk-runtime --output reports/final_recovery_acceptance/third_party_runtime_gate.json
```

预期：第二条命令退出码为 `0`，报告状态为 `PASS`。

若 Python 环境中的发行名不是 `bsk`，必须先修复安装身份；不得仅通过放置同名 `Basilisk/` 目录绕过发行版本校验。

## 4. 六项能力逐项验收

```bash
python scripts/run_six_internal_capability_acceptance.py \
  --output-root reports/final_internal_acceptance
```

Windows PowerShell 可使用一行形式：

```powershell
python scripts/run_six_internal_capability_acceptance.py --output-root reports/final_internal_acceptance
```

正式通过要求：

- 六项均为 `PASS`；
- 实际 Adapter 与 Capability Registry 登记一致；
- `uses_legacy_runner=false`；
- Adapter 不继承 Legacy Adapter；
- 三项 Basilisk 能力记录运行时版本、模块路径和必需模块导入证据；
- 除公开参考案例外，遥测均含 `time_s`；
- 时间严格不重复、单调；
- 数值非空、有限；
- 每项满足能力专用核心遥测字段组；
- TaskSpec、编译计划、摘要和遥测均生成 SHA-256。

不要在正式验收中使用 `--allow-missing-basilisk`。该参数只生成构造态证据，永远不会签发正式通过。

## 5. 全量回归

```bash
python scripts/run_full_regression_supervised.py \
  --output-root reports/final_full_regression \
  --file-timeout-s 300
```

Windows PowerShell：

```powershell
python scripts/run_full_regression_supervised.py --output-root reports/final_full_regression --file-timeout-s 300
```

脚本按测试文件启动独立 pytest 子进程，避免单一父进程在 Basilisk 或多进程资源回收阶段停滞。正式通过要求：

```text
每个测试文件退出码 = 0
failures = 0
errors = 0
skipped = 0
未排除任何测试文件
```

## 6. 一键最终验收

```bash
python scripts/run_final_recovery_acceptance.py \
  --output-root reports/final_recovery_acceptance \
  --capability-timeout-s 300 \
  --regression-file-timeout-s 300
```

Windows PowerShell：

```powershell
python scripts/run_final_recovery_acceptance.py --output-root reports/final_recovery_acceptance --capability-timeout-s 300 --regression-file-timeout-s 300
```

该入口按以下顺序 fail-closed：

```text
Basilisk 运行时身份与模块门禁
→ 六项能力逐项真实执行和遥测验收
→ 全量逐文件回归
→ 最终汇总
```

只有三层全部满足正式标准时，最终汇总才会标记为 `PASS`。

## 7. 关键证据位置

```text
reports/final_recovery_acceptance/third_party_runtime_gate.json
reports/final_internal_acceptance/six_internal_capability_acceptance.json
reports/final_internal_acceptance/six_internal_capability_acceptance.junit.xml
reports/final_full_regression/full_regression.json
reports/final_full_regression/full_regression.junit.xml
reports/final_recovery_acceptance/final_recovery_acceptance.json
```

每项能力的独立日志、逐项 JSON、运行目录和哈希证据保存在相应输出目录下。

## 8. 失败处理原则

- `BLOCKED_RUNTIME_DEPENDENCY`：Basilisk 未安装、版本不被接受或必需模块不可导入；不计为功能通过。
- `FAILED_VALIDATION`：默认表单、Capability 合同或 TaskSpec 校验失败；先修合同，禁止绕过。
- `FAILED_EXECUTION`：Adapter 已进入运行但物理执行失败；保留原始异常和运行证据。
- `FAILED_TELEMETRY`：运行完成但遥测字段、时间轴或数值质量不满足验收标准。
- 测试父进程停滞不计为通过或失败；使用逐文件监督结果和完整 JUnit 判定。
