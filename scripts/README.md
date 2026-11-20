# 项目脚本目录

所有非生产环境脚本统一存放在本目录。生产代码和正式 CLI 入口位于 `src/`，自动化测试位于 `tests/`。

## 使用规则

1. 文件名必须描述动作和对象，例如 `run_platform_acceptance.py`。
2. Python、Shell 和 PowerShell 脚本必须在文件头说明用途、参数和输出。
3. 废弃脚本应删除；确需保留时放入 `scripts/deprecated/`，并说明替代项和保留理由。
4. 新增或调整脚本后运行：

```bash
python scripts/check_script_dependency_governance.py
```

5. 依赖漏洞扫描使用：

```bash
python scripts/scan_dependencies.py --requirements requirements-lock.txt
```

## 自动门禁

- `.github/workflows/project-governance.yml`：在涉及脚本、依赖或工作流的 Pull Request 中执行严格检查。
- `.github/workflows/dependency-audit.yml`：每周一执行治理检查和 `pip-audit` 漏洞扫描。
- 两个治理脚本不导入 `sat_sim`，可在仅有 Python 和审计工具的洁净环境中运行。

扫描状态说明：`PASS` 才表示完成且未发现已知漏洞；`NOT_EXECUTED_*` 表示工具、网络或输入条件不足，不能解释为漏洞数为零。

## 环境数据资产

- python scripts/fetch_environment_assets.py：获取/验证离线 SPICE、WMM2025 和地球 J2 数据。
- python scripts/fetch_space_weather_asset.py：获取 CelesTrak SW-Last5Years.csv，验证 Basilisk NRLMSISE-00 所需列与有效观测行，并生成 space_weather_manifest.json。
- python scripts/fetch_space_weather_asset.py --offline：只检查已有 CSV，不联网；若确知已有文件来源，可显式传 --source-url，未提供时 manifest 保留 source_url=null。
- python scripts/fetch_space_weather_asset.py --refresh：显式下载新快照；新文件先验证再替换旧文件。

NRLMSISE-00 运行时只读取本地文件，不会自动联网。运行前还会按 UTC epoch、仿真时长和 Basilisk 的三日 Ap 回看要求检查完整日期覆盖；文件缺失或覆盖不足会失败关闭。Run Bundle 会记录空间天气文件 SHA-256。该数据是空间天气模型输入，不是卫星硬件标定或飞行数据。
注意：manifest 中的 source_url 和 retrieved_at_utc 是本地获取记录；SHA-256 用于绑定本地 CSV 与 manifest，不是 CelesTrak 上游数字签名，也不构成上游发布者身份认证。没有 manifest 时，运行证据会明确标记 provenance_manifest_status=NOT_PROVIDED，不会补造来源或检索时间。
