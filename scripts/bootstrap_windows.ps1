<#
.SYNOPSIS
创建 Windows 本地虚拟环境并安装项目运行与 API 依赖。
.DESCRIPTION
用途：创建 Windows 本地虚拟环境并安装项目运行与 API 依赖。
参数：无显式参数；可通过 VENV_DIR 环境变量指定虚拟环境。
输出：创建虚拟环境并安装依赖。
#>
$ErrorActionPreference = "Stop"

function Invoke-CheckedNative {
    param(
        [Parameter(Mandatory = $true)][string]$Executable,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Step
    )

    Write-Host "[sat-sim] $Step" -ForegroundColor Cyan
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Step 失败，退出码：$LASTEXITCODE"
    }
}

$Root = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$PreviousLocation = Get-Location
$Python = if ($env:PYTHON_BIN) { $env:PYTHON_BIN } else { "python" }
$Venv = if ($env:VENV_DIR) {
    if ([System.IO.Path]::IsPathRooted($env:VENV_DIR)) { $env:VENV_DIR } else { Join-Path $Root $env:VENV_DIR }
} else {
    Join-Path $Root ".venv"
}
$Constraints = Join-Path $Root "constraints.txt"
$Wheelhouse = Join-Path $Root "third_party\wheels"
$VenvPython = Join-Path $Venv "Scripts\python.exe"

try {
    Set-Location $Root

    $VersionText = & $Python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
    if ($LASTEXITCODE -ne 0 -or -not $VersionText) {
        throw "无法执行 Python。请安装 64 位 Python 3.10、3.11、3.12 或 3.13，或通过 PYTHON_BIN 指定解释器。"
    }
    $DetectedVersion = [Version]($VersionText.Trim())
    if ($DetectedVersion -lt [Version]"3.10" -or $DetectedVersion -ge [Version]"3.14") {
        throw "检测到 Python $DetectedVersion；当前版本支持 Python 3.10～3.13。"
    }
    Write-Host "[sat-sim] Python：$DetectedVersion；虚拟环境：$Venv" -ForegroundColor Green

    if ($env:SAT_SIM_RECREATE_VENV -and $env:SAT_SIM_RECREATE_VENV -ne "0" -and (Test-Path $Venv)) {
        Write-Host "[sat-sim] 删除旧虚拟环境：$Venv" -ForegroundColor Yellow
        Remove-Item -Recurse -Force $Venv
    }

    if (-not (Test-Path $VenvPython)) {
        Invoke-CheckedNative -Executable $Python -Arguments @("-m", "venv", $Venv) -Step "创建虚拟环境"
    } else {
        $VenvVersionText = & $VenvPython -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
        if ($LASTEXITCODE -ne 0) { throw "现有虚拟环境不可用：$Venv" }
        $VenvVersion = [Version]($VenvVersionText.Trim())
        if ($VenvVersion.Major -ne $DetectedVersion.Major -or $VenvVersion.Minor -ne $DetectedVersion.Minor) {
            throw "现有虚拟环境使用 Python $VenvVersion，但当前解释器为 $DetectedVersion。请设置 `$env:SAT_SIM_RECREATE_VENV='1' 后重新执行。"
        }
    }

    Invoke-CheckedNative -Executable $VenvPython -Arguments @(
        "-m", "pip", "install", "--upgrade",
        "pip==26.2.1", "setuptools==83.0.0", "wheel==0.46.2"
    ) -Step "更新 Python 构建工具"

    Invoke-CheckedNative -Executable $VenvPython -Arguments @(
        (Join-Path $Root "scripts\ensure_basilisk_runtime.py")
    ) -Step "安装并验证 Basilisk 原生运行时"

    Invoke-CheckedNative -Executable $VenvPython -Arguments @(
        "-m", "pip", "install", "--find-links", $Wheelhouse, "-c", $Constraints, "-e", ".[api,dev]"
    ) -Step "安装平台及 API/开发依赖"

    Invoke-CheckedNative -Executable $VenvPython -Arguments @(
        "-c", "import fastapi, starlette, uvicorn, httpx, numpy; print({'numpy': numpy.__version__, 'fastapi': fastapi.__version__, 'starlette': starlette.__version__, 'uvicorn': uvicorn.__version__, 'httpx': httpx.__version__})"
    ) -Step "检查 API 与 NumPy 依赖"

    Invoke-CheckedNative -Executable $VenvPython -Arguments @(
        "-m", "sat_sim.agent_cli", "doctor", "--require-api", "--strict-assets"
    ) -Step "执行环境诊断"

    Write-Host "[sat-sim] 安装完成。执行 .\scripts\start_local_windows.ps1 启动服务。" -ForegroundColor Green
}
catch {
    Write-Host "" 
    Write-Host "[sat-sim] 安装未完成：$($_.Exception.Message)" -ForegroundColor Red
    Write-Host "[sat-sim] 修复后可直接重试；需要重建虚拟环境时执行：" -ForegroundColor Yellow
    Write-Host "  `$env:SAT_SIM_RECREATE_VENV='1'" -ForegroundColor Yellow
    Write-Host "  .\scripts\bootstrap_windows.ps1" -ForegroundColor Yellow
    exit 1
}
finally {
    Set-Location $PreviousLocation
}
