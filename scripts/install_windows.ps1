<#
.SYNOPSIS
从当前源码或 Wheel 安装 Windows 运行环境。
.DESCRIPTION
用途：从当前源码或 Wheel 安装 Windows 运行环境。
参数：无显式参数；读取 VENV_DIR。
输出：完成安装并提示启动命令。
#>
$ErrorActionPreference = "Stop"
Write-Host "[sat-sim] 安装/修复本地运行环境" -ForegroundColor Cyan
if (-not $env:SAT_SIM_RECREATE_VENV) { $env:SAT_SIM_RECREATE_VENV = "0" }
& "$PSScriptRoot\bootstrap_windows.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "安装失败，退出码：$LASTEXITCODE"
}
Write-Host "[sat-sim] 安装完成。执行 .\scripts\start_windows.ps1 启动后台服务。" -ForegroundColor Green
