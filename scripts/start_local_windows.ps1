<#
.SYNOPSIS
以前台方式启动 Windows 本地工作台服务。
.DESCRIPTION
用途：以前台方式启动 Windows 本地工作台服务。
参数：环境变量 VENV_DIR、SAT_SIM_HOST、SAT_SIM_PORT、SAT_SIM_RUNS_ROOT、SAT_SIM_API_ARTIFACTS_ROOT。
输出：启动 sat_sim.agent_cli serve。
#>
$ErrorActionPreference = "Stop"
$Venv = if ($env:VENV_DIR) { $env:VENV_DIR } else { ".venv" }
$HostName = if ($env:SAT_SIM_HOST) { $env:SAT_SIM_HOST } else { "127.0.0.1" }
$Port = if ($env:SAT_SIM_PORT) { $env:SAT_SIM_PORT } else { "8000" }
$Runs = if ($env:SAT_SIM_RUNS_ROOT) { $env:SAT_SIM_RUNS_ROOT } else { "runs" }
$Artifacts = if ($env:SAT_SIM_API_ARTIFACTS_ROOT) { $env:SAT_SIM_API_ARTIFACTS_ROOT } else { ".sat_sim_api" }
& "$Venv\Scripts\python.exe" -c "from sat_sim.api import create_app; create_app(embedded_worker=False); print('[sat-sim] FastAPI lifespan preflight: PASS')"
& "$Venv\Scripts\python.exe" -m sat_sim.agent_cli serve --host $HostName --port $Port --runs-root $Runs --artifacts-root $Artifacts
