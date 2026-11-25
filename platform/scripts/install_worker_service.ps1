param(
    [string]$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path,
    [string]$ServiceName = "FundResearchWorker",
    [string]$NssmPath = "",
    [string]$DatabaseUrl = "",
    [string]$RuntimeRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path $ProjectRoot).Path
$StateRoot = $env:FACTSHEET_STATE_ROOT
if (-not $StateRoot) {
    $StateRoot = Join-Path (Split-Path (Split-Path $ProjectRoot -Parent) -Parent) "fund-research-state"
}
$StateRoot = [System.IO.Path]::GetFullPath($StateRoot)
if (-not $RuntimeRoot) {
    $RuntimeRoot = $env:FACTSHEET_RUNTIME_ROOT
}
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $StateRoot "runtime"
}
$RuntimeRoot = (Resolve-Path $RuntimeRoot -ErrorAction SilentlyContinue).Path
if (-not $RuntimeRoot) {
    throw "Runtime root not found. Pass -RuntimeRoot or set FACTSHEET_RUNTIME_ROOT."
}
$PythonExe = $env:PLATFORM_PYTHON_EXE
if (-not $PythonExe) {
    $PythonExe = Join-Path $RuntimeRoot "python\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = Join-Path $StateRoot "dependencies\venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    $PythonExe = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
}
if (-not (Test-Path -LiteralPath $PythonExe)) {
    throw "Python runtime not found under .runtime\python or .venv\Scripts."
}

if (-not $NssmPath) {
    $NssmPath = Join-Path $RuntimeRoot "bin\nssm.exe"
}
if (-not (Test-Path -LiteralPath $NssmPath)) {
    throw "NSSM service wrapper is required. Place nssm.exe at $ProjectRoot\.runtime\bin\nssm.exe or pass -NssmPath."
}

$BrowserPath = Join-Path $RuntimeRoot "ms-playwright"
$LogDir = $env:PLATFORM_RUNTIME_ROOT
if (-not $LogDir) {
    $LogDir = Join-Path $StateRoot "platform-runtime"
}
New-Item -ItemType Directory -Path $LogDir -Force | Out-Null
if (-not $DatabaseUrl) {
    $DatabaseUrl = $env:PLATFORM_DATABASE_URL
}
if (-not $DatabaseUrl) {
    $DatabaseUrl = "postgresql+psycopg://127.0.0.1:5432/fund_research"
}

& $NssmPath stop $ServiceName 2>$null | Out-Null
& $NssmPath remove $ServiceName confirm 2>$null | Out-Null
& $NssmPath install $ServiceName $PythonExe "-m app.worker.main --browser-preflight --worker-id worker-windows-service"
& $NssmPath set $ServiceName AppDirectory $ProjectRoot
& $NssmPath set $ServiceName AppExit Default Restart
& $NssmPath set $ServiceName AppStdout (Join-Path $LogDir "worker-service.log")
& $NssmPath set $ServiceName AppStderr (Join-Path $LogDir "worker-service.err.log")
& $NssmPath set $ServiceName DisplayName "Fund Research Data Worker"
& $NssmPath set $ServiceName Start SERVICE_AUTO_START
& $NssmPath set $ServiceName AppEnvironmentExtra "PYTHONPATH=$ProjectRoot\platform\api" "FACTSHEET_PROJECT_ROOT=$ProjectRoot" "FACTSHEET_STATE_ROOT=$StateRoot" "FACTSHEET_DATA_ROOT=$(Join-Path $StateRoot 'data')" "FACTSHEET_RUNTIME_ROOT=$RuntimeRoot" "PLATFORM_RUNTIME_ROOT=$LogDir" "PLATFORM_DATABASE_URL=$DatabaseUrl" "PLAYWRIGHT_BROWSERS_PATH=$BrowserPath" "PLATFORM_BROWSER_PREFLIGHT=required" "PYTHONUNBUFFERED=1"
& $NssmPath start $ServiceName
Write-Host "[OK] Windows Worker service installed and started: $ServiceName"
Write-Host "     Logs: $LogDir\worker-service.log"

