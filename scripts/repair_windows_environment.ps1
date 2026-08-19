<#
.SYNOPSIS
重建损坏或不一致的 Windows 虚拟环境。
.DESCRIPTION
用途：重建损坏或不一致的 Windows 虚拟环境。
参数：无显式参数；读取 VENV_DIR。
输出：删除并重建虚拟环境。
#>
$ErrorActionPreference = "Stop"
$env:SAT_SIM_RECREATE_VENV = "1"
& "$PSScriptRoot\bootstrap_windows.ps1"
if ($LASTEXITCODE -ne 0) {
    throw "环境重建失败，退出码：$LASTEXITCODE"
}
Write-Host "[sat-sim] 环境重建完成。执行 .\scripts\start_local_windows.ps1 启动服务。" -ForegroundColor Green
