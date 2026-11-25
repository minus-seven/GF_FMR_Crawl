param(
    [string]$PythonVersion = "3.12.10",
    [switch]$Force,
    [string]$RuntimeRoot = ""
)

$ErrorActionPreference = "Stop"
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateRoot = $env:FACTSHEET_STATE_ROOT
if (-not $StateRoot) {
    $StateRoot = Join-Path (Split-Path $ProjectRoot -Parent) "state"
}
$StateRoot = [System.IO.Path]::GetFullPath($StateRoot)
if (-not $RuntimeRoot) {
    $RuntimeRoot = $env:FACTSHEET_RUNTIME_ROOT
}
if (-not $RuntimeRoot) {
    $RuntimeRoot = Join-Path $StateRoot "runtime"
}
$RuntimeRoot = [System.IO.Path]::GetFullPath($RuntimeRoot)
$PythonRoot = Join-Path $RuntimeRoot "python"
$WheelRoot = Join-Path $RuntimeRoot "wheels"
$BrowserRoot = Join-Path $RuntimeRoot "ms-playwright"
$Requirements = Join-Path $ProjectRoot "requirements.lock.txt"
$PlatformRequirements = Join-Path $ProjectRoot "platform\requirements.lock.txt"

if (-not (Test-Path -LiteralPath $Requirements)) {
    throw "Missing $Requirements"
}
if (-not (Test-Path -LiteralPath $PlatformRequirements)) {
    throw "Missing $PlatformRequirements"
}

if (Test-Path -LiteralPath $RuntimeRoot) {
    $ResolvedRuntime = (Resolve-Path -LiteralPath $RuntimeRoot).Path
    $allowedRoots = @($ProjectRoot, $StateRoot) | ForEach-Object { [System.IO.Path]::GetFullPath($_).TrimEnd('\') + '\' }
    if (-not ($allowedRoots | Where-Object { $ResolvedRuntime.StartsWith($_, [System.StringComparison]::OrdinalIgnoreCase) })) {
        throw "Unsafe runtime path: $ResolvedRuntime"
    }
    if (-not $Force) {
        throw "$RuntimeRoot already exists. Re-run with -Force to rebuild it."
    }
    Remove-Item -LiteralPath $ResolvedRuntime -Recurse -Force
}

New-Item -ItemType Directory -Path $PythonRoot, $WheelRoot, $BrowserRoot -Force | Out-Null
$TempRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("factsheet-runtime-" + [guid]::NewGuid())
New-Item -ItemType Directory -Path $TempRoot -Force | Out-Null

try {
    $PythonZip = Join-Path $TempRoot "python-embed.zip"
    $GetPip = Join-Path $TempRoot "get-pip.py"
    $PythonUrl = "https://www.python.org/ftp/python/$PythonVersion/python-$PythonVersion-embed-amd64.zip"
    Write-Host "[1/6] Downloading portable Python $PythonVersion..."
    Invoke-WebRequest -Uri $PythonUrl -OutFile $PythonZip
    Expand-Archive -LiteralPath $PythonZip -DestinationPath $PythonRoot

    $PthFile = Get-ChildItem -LiteralPath $PythonRoot -Filter "python*._pth" | Select-Object -First 1
    if (-not $PthFile) {
        throw "Embedded Python ._pth file not found."
    }
    $PthLines = Get-Content -LiteralPath $PthFile.FullName |
        Where-Object {
            $_ -notmatch "^\s*#?\s*import site\s*$" -and
            $_ -ne "Lib\site-packages" -and
            $_ -ne "..\..\code"
        }
    $PthLines += "Lib\site-packages"
    $PthLines += "..\..\code"
    $PthLines += "import site"
    Set-Content -LiteralPath $PthFile.FullName -Value $PthLines -Encoding ASCII

    Write-Host "[2/6] Bootstrapping pip..."
    Invoke-WebRequest -Uri "https://bootstrap.pypa.io/get-pip.py" -OutFile $GetPip
    & (Join-Path $PythonRoot "python.exe") $GetPip --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { throw "get-pip failed with exit code $LASTEXITCODE" }

    Write-Host "[3/6] Downloading locked offline wheels..."
    & (Join-Path $PythonRoot "python.exe") -m pip download `
        --disable-pip-version-check `
        --dest $WheelRoot `
        --requirement $Requirements `
        --requirement $PlatformRequirements
    if ($LASTEXITCODE -ne 0) { throw "pip download failed with exit code $LASTEXITCODE" }

    Write-Host "[4/6] Installing dependencies into portable Python..."
    & (Join-Path $PythonRoot "python.exe") -m pip install `
        --disable-pip-version-check `
        --no-index `
        --find-links $WheelRoot `
        --requirement $Requirements `
        --requirement $PlatformRequirements
    if ($LASTEXITCODE -ne 0) { throw "offline pip install failed with exit code $LASTEXITCODE" }

    Write-Host "[5/6] Downloading the portable Chromium browser..."
    $env:PLAYWRIGHT_BROWSERS_PATH = $BrowserRoot
    & (Join-Path $PythonRoot "python.exe") -m playwright install chromium
    if ($LASTEXITCODE -ne 0) { throw "Playwright browser download failed with exit code $LASTEXITCODE" }

    Write-Host "[6/6] Validating the finished offline runtime..."
    & (Join-Path $PythonRoot "python.exe") `
        (Join-Path $ProjectRoot "code\setup_environment.py") `
        --require-portable
    if ($LASTEXITCODE -ne 0) { throw "Portable runtime validation failed with exit code $LASTEXITCODE" }

    $Manifest = @{
        built_at = (Get-Date).ToString("o")
        python_version = $PythonVersion
        architecture = "amd64"
        requirements_sha256 = (Get-FileHash -LiteralPath $Requirements -Algorithm SHA256).Hash
        platform_requirements_sha256 = (Get-FileHash -LiteralPath $PlatformRequirements -Algorithm SHA256).Hash
        wheel_count = (Get-ChildItem -LiteralPath $WheelRoot -Filter "*.whl").Count
    } | ConvertTo-Json
    Set-Content -LiteralPath (Join-Path $RuntimeRoot "BUILD.json") -Value $Manifest -Encoding UTF8
    Write-Host ""
    Write-Host "[OK] Portable offline runtime is ready at $RuntimeRoot"
    Write-Host "Copy the code package and external state directory to the target Windows PC."
}
finally {
    if (Test-Path -LiteralPath $TempRoot) {
        Remove-Item -LiteralPath $TempRoot -Recurse -Force
    }
}
