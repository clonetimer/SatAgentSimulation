<#
.SYNOPSIS
以后台进程方式启动 Windows 工作台并执行健康检查。
.DESCRIPTION
用途：以后台进程方式启动 Windows 工作台并执行健康检查。
参数：环境变量 VENV_DIR 与 SAT_SIM_*。
输出：写入 PID/日志并启动服务。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = if ($env:VENV_DIR) { Join-Path $Root $env:VENV_DIR } else { Join-Path $Root ".venv" }
$Python = Join-Path $Venv "Scripts\python.exe"
$HostName = if ($env:SAT_SIM_HOST) { $env:SAT_SIM_HOST } else { "127.0.0.1" }
$Port = if ($env:SAT_SIM_PORT) { $env:SAT_SIM_PORT } else { "8000" }
$Runs = if ($env:SAT_SIM_RUNS_ROOT) { $env:SAT_SIM_RUNS_ROOT } else { Join-Path $Root "runs" }
$Artifacts = if ($env:SAT_SIM_API_ARTIFACTS_ROOT) { $env:SAT_SIM_API_ARTIFACTS_ROOT } else { Join-Path $Root ".sat_sim_api" }
$PidFile = Join-Path $Artifacts "control_plane.pid"
$LogDir = Join-Path $Artifacts "logs"
$Stdout = Join-Path $LogDir "control_plane.stdout.log"
$Stderr = Join-Path $LogDir "control_plane.stderr.log"
if (-not (Test-Path $Python)) { throw "未找到 $Python，请先执行 .\scripts\install_windows.ps1" }
New-Item -ItemType Directory -Force -Path $Artifacts, $LogDir, $Runs | Out-Null
if (Test-Path $PidFile) {
    $Existing = [int](Get-Content $PidFile -ErrorAction SilentlyContinue)
    if ($Existing -and (Get-Process -Id $Existing -ErrorAction SilentlyContinue)) {
        Write-Host "[sat-sim] 服务已经运行，PID=$Existing" -ForegroundColor Yellow
        exit 0
    }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
}
& $Python -c "from sat_sim.api import create_app; create_app(embedded_worker=False); print('[sat-sim] FastAPI lifespan preflight: PASS')"
if ($LASTEXITCODE -ne 0) { throw "FastAPI 启动前检查失败，退出码：$LASTEXITCODE" }
$Args = @("-m", "sat_sim.agent_cli", "serve", "--host", $HostName, "--port", $Port, "--runs-root", $Runs, "--artifacts-root", $Artifacts)
$Process = Start-Process -FilePath $Python -ArgumentList $Args -WorkingDirectory $Root -RedirectStandardOutput $Stdout -RedirectStandardError $Stderr -PassThru -WindowStyle Hidden
Set-Content -Path $PidFile -Value $Process.Id -Encoding ascii
$Healthy = $false
for ($i=0; $i -lt 30; $i++) {
    Start-Sleep -Milliseconds 500
    try {
        $Response = Invoke-RestMethod -Uri "http://$HostName`:$Port/health" -TimeoutSec 2
        if ($Response.ok) { $Healthy = $true; break }
    } catch { }
    if ($Process.HasExited) { break }
}
if (-not $Healthy) {
    Write-Host "[sat-sim] 启动失败，请查看：$Stderr" -ForegroundColor Red
    if (-not $Process.HasExited) { Stop-Process -Id $Process.Id -Force -ErrorAction SilentlyContinue }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    exit 1
}
Write-Host "[sat-sim] 已启动：http://$HostName`:$Port/  PID=$($Process.Id)" -ForegroundColor Green
if (-not $env:SAT_SIM_NO_BROWSER) { Start-Process "http://$HostName`:$Port/" }
