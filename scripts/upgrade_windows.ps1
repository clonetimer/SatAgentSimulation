<#
.SYNOPSIS
使用指定 Wheel 升级 Windows 安装并执行版本与健康检查。
.DESCRIPTION
用途：使用指定 Wheel 升级 Windows 安装并执行版本与健康检查。
参数：Wheel、ExpectedVersion。
输出：备份运行数据、升级包并重启服务。
#>
param(
    [string]$Wheel = "",
    [string]$ExpectedVersion = "0.7.8"
)
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = if ($env:VENV_DIR) { Join-Path $Root $env:VENV_DIR } else { Join-Path $Root ".venv" }
$Python = Join-Path $Venv "Scripts\python.exe"
$HostName = if ($env:SAT_SIM_HOST) { $env:SAT_SIM_HOST } else { "127.0.0.1" }
$Port = if ($env:SAT_SIM_PORT) { [int]$env:SAT_SIM_PORT } else { 8000 }

function Invoke-CheckedNative {
    param([string]$Program, [string[]]$Arguments, [string]$Step)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Step 失败，退出码：$LASTEXITCODE"
    }
}

function Stop-ResidualSatSimListener {
    try {
        $Release = Invoke-RestMethod -Uri "http://$HostName`:$Port/release" -TimeoutSec 2
        if (-not $Release.release) { return }
        $Connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
        foreach ($Connection in $Connections) {
            if ($Connection.OwningProcess) {
                Write-Host "[sat-sim] 检测到仍占用端口的旧工作台进程 PID=$($Connection.OwningProcess)，正在停止。" -ForegroundColor Yellow
                Stop-Process -Id $Connection.OwningProcess -Force -ErrorAction Stop
            }
        }
        Start-Sleep -Milliseconds 800
    } catch {
        # 端口未被卫星仿真服务占用时无需处理。
    }
}

if (-not (Test-Path $Python)) { throw "未找到虚拟环境，请先执行 install_windows.ps1 或 bootstrap_windows.ps1。" }

& "$PSScriptRoot\stop_windows.ps1"
if ($LASTEXITCODE -ne 0) { throw "停止现有服务失败，退出码：$LASTEXITCODE" }
Stop-ResidualSatSimListener

$BackupRoot = Join-Path $Root ("backups\upgrade_" + (Get-Date -Format "yyyyMMdd_HHmmss"))
New-Item -ItemType Directory -Force -Path $BackupRoot | Out-Null
foreach ($Name in @(".sat_sim_api", "runs")) {
    $Source = Join-Path $Root $Name
    if (Test-Path $Source) { Copy-Item -Recurse -Force $Source (Join-Path $BackupRoot $Name) }
}

if (-not $Wheel) {
    $Wheel = Get-ChildItem (Join-Path $Root "dist\satellite_simulation_platform-*.whl") |
        Sort-Object LastWriteTime -Descending |
        Select-Object -First 1 -ExpandProperty FullName
}
if (-not $Wheel -or -not (Test-Path $Wheel)) { throw "未找到升级 Wheel，请使用 -Wheel 指定。" }

Write-Host "[sat-sim] 安装维护版本：$Wheel" -ForegroundColor Cyan
Invoke-CheckedNative $Python @("-m", "pip", "install", "--force-reinstall", "--no-deps", $Wheel) "安装 Wheel"

$InstalledVersion = (& $Python -c "import sat_sim; print(sat_sim.__version__)" 2>&1 | Select-Object -Last 1).Trim()
if ($LASTEXITCODE -ne 0) { throw "读取已安装版本失败。" }
if ($InstalledVersion -ne $ExpectedVersion) {
    throw "安装版本核验失败：期望 $ExpectedVersion，实际 $InstalledVersion。请确认 Wheel 路径与虚拟环境。"
}
Write-Host "[sat-sim] Python 包版本核验通过：$InstalledVersion" -ForegroundColor Green

Invoke-CheckedNative $Python @("-m", "sat_sim.agent_cli", "doctor", "--require-api", "--strict-assets") "Environment Doctor"

$env:SAT_SIM_NO_BROWSER = "1"
& "$PSScriptRoot\start_windows.ps1"
if ($LASTEXITCODE -ne 0) { throw "启动服务失败，退出码：$LASTEXITCODE" }

$ReleaseVersion = $null
for ($i = 0; $i -lt 20; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $Response = Invoke-RestMethod -Uri "http://$HostName`:$Port/release" -TimeoutSec 2
        $ReleaseVersion = [string]$Response.release.release_version
        if ($ReleaseVersion) { break }
    } catch { }
}
if ($ReleaseVersion -ne $ExpectedVersion) {
    throw "服务版本核验失败：期望 $ExpectedVersion，接口返回 $ReleaseVersion。可能仍有旧服务占用端口。"
}

Write-Host "[sat-sim] 升级完成：界面与服务版本均应为 $ExpectedVersion。" -ForegroundColor Green
Write-Host "[sat-sim] 备份位于：$BackupRoot" -ForegroundColor Green
Write-Host "[sat-sim] 请打开 http://$HostName`:$Port/ 并按 Ctrl+F5 强制刷新。右上角应显示：工作台 $ExpectedVersion · 服务 $ExpectedVersion。" -ForegroundColor Cyan
