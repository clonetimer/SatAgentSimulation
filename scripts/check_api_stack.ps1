<#
.SYNOPSIS
检查 Windows 虚拟环境中的 FastAPI、Pydantic 与项目 API 栈。
.DESCRIPTION
用途：检查 Windows 虚拟环境中的 FastAPI、Pydantic 与项目 API 栈。
参数：无显式参数；读取 VENV_DIR。
输出：控制台检查结果，失败时返回非零退出码。
#>
$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Venv = if ($env:VENV_DIR) {
    if ([System.IO.Path]::IsPathRooted($env:VENV_DIR)) { $env:VENV_DIR } else { Join-Path $Root $env:VENV_DIR }
} else { Join-Path $Root ".venv" }
$Python = Join-Path $Venv "Scripts\python.exe"
if (-not (Test-Path $Python)) {
    throw "未找到虚拟环境：$Python。请先执行 .\scripts\bootstrap_windows.ps1。"
}
& $Python -c "import json, fastapi, starlette, uvicorn, httpx, numpy; from sat_sim.api import create_app; app=create_app(embedded_worker=False); print(json.dumps({'ok': True, 'numpy': numpy.__version__, 'fastapi': fastapi.__version__, 'starlette': starlette.__version__, 'uvicorn': uvicorn.__version__, 'httpx': httpx.__version__, 'app_type': type(app).__name__, 'lifespan_configured': app.router.lifespan_context is not None}, ensure_ascii=False, indent=2))"
if ($LASTEXITCODE -ne 0) { throw "API 依赖检查失败，退出码：$LASTEXITCODE" }
