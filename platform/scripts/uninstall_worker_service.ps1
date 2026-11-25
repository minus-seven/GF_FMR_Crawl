param(
    [string]$ServiceName = "FundResearchWorker",
    [string]$NssmPath = ""
)

$ErrorActionPreference = "Stop"
if (-not $NssmPath) {
    $ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
    $StateRoot = $env:FACTSHEET_STATE_ROOT
    if (-not $StateRoot) {
        $StateRoot = Join-Path (Split-Path (Split-Path $ProjectRoot -Parent) -Parent) "fund-research-state"
    }
    $RuntimeRoot = $env:FACTSHEET_RUNTIME_ROOT
    if (-not $RuntimeRoot) {
        $RuntimeRoot = Join-Path $StateRoot "runtime"
    }
    $NssmPath = Join-Path $RuntimeRoot "bin\nssm.exe"
}
if (-not (Test-Path -LiteralPath $NssmPath)) {
    throw "NSSM service wrapper not found: $NssmPath"
}
& $NssmPath stop $ServiceName 2>$null | Out-Null
& $NssmPath remove $ServiceName confirm
Write-Host "[OK] Windows Worker service removed: $ServiceName"

