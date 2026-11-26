param(
    [string]$OutputDirectory = (Join-Path $PSScriptRoot '..\..\dist')
)

$ErrorActionPreference = 'Stop'
$Root = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$Gcc = (Get-Command gcc.exe -ErrorAction SilentlyContinue).Source
$Windres = (Get-Command windres.exe -ErrorAction SilentlyContinue).Source
if (-not $Gcc -or -not $Windres) {
    throw 'gcc.exe and windres.exe are required. Install MinGW-w64 and add its bin directory to PATH.'
}

if ([System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
} else {
    $OutputDirectory = [System.IO.Path]::GetFullPath((Join-Path $Root $OutputDirectory))
}
$BuildDirectory = Join-Path $Root 'state\runtime\server-exe-build'
New-Item -ItemType Directory -Force -Path $OutputDirectory,$BuildDirectory | Out-Null
$Resource = Join-Path $BuildDirectory 'server_launcher.res'
$Exe = Join-Path $OutputDirectory 'GF_FMR_Server.exe'
& $Windres (Join-Path $PSScriptRoot 'server_launcher.rc') -O coff -o $Resource
if ($LASTEXITCODE -ne 0) { throw "windres failed: $LASTEXITCODE" }
& $Gcc -municode -mwindows (Join-Path $PSScriptRoot 'server_launcher.c') $Resource -o $Exe
if ($LASTEXITCODE -ne 0) { throw "gcc failed: $LASTEXITCODE" }
Write-Host "Built: $Exe"
