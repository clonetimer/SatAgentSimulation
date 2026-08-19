#!/usr/bin/env bash
# 用途：以前台方式启动 Linux/macOS 本地工作台服务。
# 参数：环境变量 VENV_DIR、SAT_SIM_HOST、SAT_SIM_PORT、SAT_SIM_RUNS_ROOT、SAT_SIM_API_ARTIFACTS_ROOT。
# 输出：启动 sat_sim.agent_cli serve。
set -euo pipefail
VENV_DIR="${VENV_DIR:-.venv}"
HOST="${SAT_SIM_HOST:-127.0.0.1}"
PORT="${SAT_SIM_PORT:-8000}"
RUNS_ROOT="${SAT_SIM_RUNS_ROOT:-runs}"
ARTIFACTS_ROOT="${SAT_SIM_API_ARTIFACTS_ROOT:-.sat_sim_api}"
exec "$VENV_DIR/bin/python" -m sat_sim.agent_cli serve \
  --host "$HOST" --port "$PORT" \
  --runs-root "$RUNS_ROOT" --artifacts-root "$ARTIFACTS_ROOT"
