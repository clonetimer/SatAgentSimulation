<#
.SYNOPSIS
停止 Windows 工作台后台进程。
.DESCRIPTION
用途：停止 Windows 工作台后台进程。
参数：环境变量 SAT_SIM_API_ARTIFACTS_ROOT。
输出：停止进程并清理 PID 文件。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Artifacts = if ($env:SAT_SIM_API_ARTIFACTS_ROOT) { $env:SAT_SIM_API_ARTIFACTS_ROOT } else { Join-Path $Root ".sat_sim_api" }
$PidFile = Join-Path $Artifacts "control_plane.pid"
if (-not (Test-Path $PidFile)) { Write-Host "[sat-sim] 未发现运行中的服务。"; exit 0 }
$PidValue = [int](Get-Content $PidFile)
$Process = Get-Process -Id $PidValue -ErrorAction SilentlyContinue
if ($Process) {
    Stop-Process -Id $PidValue
    $Process.WaitForExit(10000) | Out-Null
    if (Get-Process -Id $PidValue -ErrorAction SilentlyContinue) { Stop-Process -Id $PidValue -Force }
}
Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
Write-Host "[sat-sim] 服务已停止。" -ForegroundColor Green
