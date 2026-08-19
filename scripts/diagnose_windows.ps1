<#
.SYNOPSIS
收集 Windows 服务状态、环境 Doctor 和关键日志诊断信息。
.DESCRIPTION
用途：收集 Windows 服务状态、环境 Doctor 和关键日志诊断信息。
参数：无显式参数；读取 VENV_DIR 与 SAT_SIM_* 环境变量。
输出：在 reports 或控制台输出诊断结果。
#>
$ErrorActionPreference = "Stop"
$HostName = if ($env:SAT_SIM_HOST) { $env:SAT_SIM_HOST } else { "127.0.0.1" }
$Port = if ($env:SAT_SIM_PORT) { $env:SAT_SIM_PORT } else { "8000" }
$Headers = @{}
if ($env:SAT_SIM_API_TOKEN) { $Headers["Authorization"] = "Bearer $($env:SAT_SIM_API_TOKEN)" }
$Output = Join-Path (Get-Location) ("sat_sim_diagnostic_" + (Get-Date -Format "yyyyMMdd_HHmmss") + ".zip")
Invoke-WebRequest -Uri "http://$HostName`:$Port/diagnostics/bundle" -Headers $Headers -OutFile $Output
Write-Host "[sat-sim] 诊断包已生成：$Output" -ForegroundColor Green
