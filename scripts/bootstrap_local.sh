#!/usr/bin/env bash
# 用途：创建 Linux/macOS 本地虚拟环境并安装项目运行与 API 依赖。
# 参数：环境变量 VENV_DIR；无位置参数。
# 输出：创建虚拟环境并安装依赖。
set -euo pipefail
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-.venv}"
"$PYTHON_BIN" -m venv "$VENV_DIR"
"$VENV_DIR/bin/python" -m pip install --upgrade "pip==26.2.1" "setuptools==83.0.0" "wheel==0.46.2"
"$VENV_DIR/bin/python" scripts/ensure_basilisk_runtime.py
"$VENV_DIR/bin/python" -m pip install --find-links third_party/wheels -c constraints.txt -e '.[api,dev]'
"$VENV_DIR/bin/python" -m sat_sim.agent_cli doctor --require-api --strict-assets
