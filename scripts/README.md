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
