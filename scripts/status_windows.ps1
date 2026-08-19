<#
.SYNOPSIS
查询 Windows 工作台进程与健康状态。
.DESCRIPTION
用途：查询 Windows 工作台进程与健康状态。
参数：环境变量 SAT_SIM_HOST、SAT_SIM_PORT、SAT_SIM_API_ARTIFACTS_ROOT。
输出：输出 JSON 状态。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Artifacts = if ($env:SAT_SIM_API_ARTIFACTS_ROOT) { $env:SAT_SIM_API_ARTIFACTS_ROOT } else { Join-Path $Root ".sat_sim_api" }
$PidFile = Join-Path $Artifacts "control_plane.pid"
$HostName = if ($env:SAT_SIM_HOST) { $env:SAT_SIM_HOST } else { "127.0.0.1" }
$Port = if ($env:SAT_SIM_PORT) { $env:SAT_SIM_PORT } else { "8000" }
$PidValue = if (Test-Path $PidFile) { [int](Get-Content $PidFile) } else { $null }
$Running = $PidValue -and (Get-Process -Id $PidValue -ErrorAction SilentlyContinue)
try { $Health = Invoke-RestMethod -Uri "http://$HostName`:$Port/health" -TimeoutSec 3 } catch { $Health = $null }
[ordered]@{ running=[bool]$Running; pid=$PidValue; url="http://$HostName`:$Port/"; health_ok=[bool]($Health -and $Health.ok); api_version=$Health.api_version; workbench_version=$Health.workbench_version } | ConvertTo-Json
