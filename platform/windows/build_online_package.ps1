param(
    [string]$Version = "v2026.11.27-windows.1",
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = "Stop"
$Root = (Resolve-Path (Join-Path $PSScriptRoot "..\..")).Path
if (-not $OutputDirectory) {
    $OutputDirectory = Join-Path $Root ("dist\release\{0}" -f $Version)
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
$Stage = Join-Path $OutputDirectory "GF_FMR_Windows_Runtime"
$ZipPath = Join-Path $OutputDirectory "GF_FMR_Windows_Runtime.zip"

if (Test-Path -LiteralPath $Stage) {
    Remove-Item -LiteralPath $Stage -Recurse -Force
}
New-Item -ItemType Directory -Force -Path $Stage | Out-Null

function Copy-Tree([string]$Source, [string]$RelativeDestination, [string[]]$ExcludeDirectories = @()) {
    $destination = Join-Path $Stage $RelativeDestination
    New-Item -ItemType Directory -Force -Path $destination | Out-Null
    $arguments = @($Source, $destination, "/E", "/COPY:DAT", "/DCOPY:DAT", "/R:1", "/W:1", "/NFL", "/NDL", "/NJH", "/NJS", "/NP")
    if ($ExcludeDirectories.Count -gt 0) { $arguments += "/XD"; $arguments += $ExcludeDirectories }
    & robocopy.exe @arguments | Out-Null
    if ($LASTEXITCODE -gt 7) { throw "Directory copy failed: $Source (robocopy $LASTEXITCODE)" }
}

Copy-Item -LiteralPath (Join-Path $Root "dist\GF_FMR_Server.exe") -Destination $Stage -Force
Copy-Item -LiteralPath (Join-Path $Root "config.example.ini") -Destination $Stage -Force
Copy-Tree (Join-Path $Root "code") "code"
Copy-Tree (Join-Path $Root "platform") "platform" @((Join-Path $Root "platform\web\node_modules"))
Copy-Tree (Join-Path $Root "state\dependencies\venv") "state\dependencies\venv"
Copy-Tree (Join-Path $Root "state\dependencies\node22") "state\dependencies\node22"
Copy-Tree (Join-Path $Root "state\platform-runtime\postgresql17\pgsql") "state\platform-runtime\postgresql17\pgsql"

$ollamaDestination = Join-Path $Stage "state\dependencies\ollama"
New-Item -ItemType Directory -Force -Path $ollamaDestination | Out-Null
Copy-Item -LiteralPath (Join-Path $Root "state\dependencies\ollama\ollama.exe") -Destination $ollamaDestination -Force

New-Item -ItemType Directory -Force -Path `
    (Join-Path $Stage "state\runtime"), `
    (Join-Path $Stage "state\data"), `
    (Join-Path $Stage "state\platform-runtime\postgresql17\data") | Out-Null

Copy-Item -LiteralPath (Join-Path $Root "platform\windows\install_online.ps1") -Destination $Stage -Force
if (Test-Path -LiteralPath $ZipPath) {
    Remove-Item -LiteralPath $ZipPath -Force
}
Compress-Archive -Path (Join-Path $Stage "*") -DestinationPath $ZipPath -CompressionLevel Optimal
$hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash
Write-Host ("Package: {0}" -f $ZipPath)
Write-Host ("SHA256:  {0}" -f $hash)
