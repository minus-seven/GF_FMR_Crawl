param(
    [string]$Repository = "minus-seven/GF_FMR_Crawl",
    [string]$Version = "",
    [string]$InstallRoot = "$env:ProgramData\GF_FMR_Crawl",
    [switch]$SkipDatabase,
    [switch]$SkipModel,
    [switch]$StartServer
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

function Write-Step([string]$Message) { Write-Host ("[GF_FMR] {0}" -f $Message) -ForegroundColor Cyan }
function Get-Asset($Release, [string]$Pattern) {
    $asset = $Release.assets | Where-Object { $_.name -match $Pattern } | Select-Object -First 1
    if (-not $asset) { throw "Release $($Release.tag_name) 中找不到资产：$Pattern" }
    return $asset
}

if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
    [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw "请使用管理员 PowerShell 运行此安装器。"
}

$headers = @{ "User-Agent" = "GF-FMR-Crawl-Online-Installer"; Accept = "application/vnd.github+json" }
$apiBase = "https://api.github.com/repos/$Repository/releases"
Write-Step "读取 GitHub 版本清单"
$release = if ($Version) {
    Invoke-RestMethod -Uri "$apiBase/tags/$Version" -Headers $headers
} else {
    Invoke-RestMethod -Uri "$apiBase/latest" -Headers $headers
}

$downloadRoot = Join-Path $env:TEMP ("GF_FMR_Install_{0}" -f [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Force -Path $downloadRoot | Out-Null
try {
    $package = Get-Asset $release "^GF_FMR_Windows_Runtime\.zip$"
    $packagePath = Join-Path $downloadRoot $package.name
    Write-Step "下载 $($release.tag_name) 运行包"
    Invoke-WebRequest -Uri $package.browser_download_url -Headers $headers -OutFile $packagePath

    $expectedHash = ($release.body | Select-String -Pattern "GF_FMR_Windows_Runtime\.zip\s+SHA256:\s*([A-Fa-f0-9]{64})" -AllMatches).Matches.Value
    if ($expectedHash) {
        $actualHash = (Get-FileHash -LiteralPath $packagePath -Algorithm SHA256).Hash
        if ($actualHash -ine $expectedHash.Split(":")[-1].Trim()) { throw "运行包 SHA256 校验失败。" }
    }

    $extractRoot = Join-Path $downloadRoot "extract"
    Expand-Archive -LiteralPath $packagePath -DestinationPath $extractRoot -Force
    $payload = Join-Path $extractRoot "GF_FMR_Windows_Runtime"
    if (-not (Test-Path -LiteralPath (Join-Path $payload "GF_FMR_Server.exe"))) { throw "运行包结构不完整。" }

    Write-Step "安装到 $InstallRoot"
    New-Item -ItemType Directory -Force -Path $InstallRoot | Out-Null
    Copy-Item -Path (Join-Path $payload "*") -Destination $InstallRoot -Recurse -Force

    $stateRoot = Join-Path $InstallRoot "state"
    $runtimeRoot = Join-Path $stateRoot "runtime"
    $pgRoot = Join-Path $stateRoot "platform-runtime\postgresql17"
    $pgBin = Join-Path $pgRoot "pgsql\bin"
    $pgData = Join-Path $pgRoot "data"
    New-Item -ItemType Directory -Force -Path $runtimeRoot, $pgData, (Join-Path $stateRoot "data") | Out-Null

    $passwordFile = Join-Path $runtimeRoot "postgres-superuser.txt"
    if (-not (Test-Path -LiteralPath $passwordFile)) {
        $password = ([guid]::NewGuid().ToString("N") + "Aa9!")
        Set-Content -LiteralPath $passwordFile -Value $password -Encoding ascii -NoNewline
    } else { $password = (Get-Content -LiteralPath $passwordFile -Raw).Trim() }

    $configPath = Join-Path $stateRoot "config.ini"
    if (-not (Test-Path -LiteralPath $configPath)) {
        Copy-Item -LiteralPath (Join-Path $InstallRoot "config.example.ini") -Destination $configPath
    }

    $nodeRoot = Join-Path $stateRoot "dependencies\node22"
    $webRoot = Join-Path $InstallRoot "platform\web"
    if (-not (Test-Path -LiteralPath (Join-Path $webRoot "node_modules\vite\bin\vite.js"))) {
        Write-Step "安装锁定的前端依赖"
        $corepack = Join-Path $nodeRoot "corepack.cmd"
        if (-not (Test-Path -LiteralPath $corepack)) { throw "运行包缺少 Node.js Corepack。" }
        Push-Location $webRoot
        try {
            & $corepack enable | Out-Host
            & $corepack pnpm install --frozen-lockfile | Out-Host
            if ($LASTEXITCODE -ne 0) { throw "前端依赖安装失败。" }
        } finally { Pop-Location }
    }

    if (-not (Test-Path -LiteralPath (Join-Path $pgData "PG_VERSION"))) {
        Write-Step "初始化 PostgreSQL 数据目录"
        $pwFile = Join-Path $downloadRoot "pg-init-password.txt"
        Set-Content -LiteralPath $pwFile -Value $password -Encoding ascii -NoNewline
        & (Join-Path $pgBin "initdb.exe") -D $pgData -U postgres --pwfile=$pwFile --auth-host=scram-sha-256 --auth-local=trust | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "PostgreSQL initdb 失败。" }
    }

    if (-not $SkipDatabase) {
        $dumpAsset = Get-Asset $release "fund_research_.*\.dump$"
        $dumpPath = Join-Path $downloadRoot $dumpAsset.name
        Write-Step "下载数据库快照 $($dumpAsset.name)"
        Invoke-WebRequest -Uri $dumpAsset.browser_download_url -Headers $headers -OutFile $dumpPath
        $env:PGPASSWORD = $password
        $pgCtl = Join-Path $pgBin "pg_ctl.exe"
        $psql = Join-Path $pgBin "psql.exe"
        $createdb = Join-Path $pgBin "createdb.exe"
        $restore = Join-Path $pgBin "pg_restore.exe"
        & $pgCtl -D $pgData -l (Join-Path $runtimeRoot "postgres-installer.log") start -w | Out-Host
        & $psql -h 127.0.0.1 -U postgres -d postgres -c "SELECT 1" | Out-Host
        & $createdb -h 127.0.0.1 -U postgres fund_research 2>$null
        & $restore -h 127.0.0.1 -U postgres -d fund_research --clean --if-exists --no-owner $dumpPath | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "数据库恢复失败。" }
        & $pgCtl -D $pgData stop -m fast -w | Out-Host
        Remove-Item Env:PGPASSWORD -ErrorAction SilentlyContinue
    }

    if (-not $SkipModel) {
        $ollama = Join-Path $stateRoot "dependencies\ollama\ollama.exe"
        if (Test-Path -LiteralPath $ollama) {
            Write-Step "启动 Ollama 并尝试下载 qwen3:4b"
            $ollamaProcess = Start-Process -FilePath $ollama -ArgumentList "serve" -WorkingDirectory (Split-Path $ollama) -PassThru -WindowStyle Hidden
            Start-Sleep -Seconds 3
            & $ollama pull qwen3:4b | Out-Host
            if ($ollamaProcess -and -not $ollamaProcess.HasExited) { Stop-Process -Id $ollamaProcess.Id -Force -ErrorAction SilentlyContinue }
        }
    }

    $installed = [ordered]@{
        repository = $Repository
        release = $release.tag_name
        installed_at = (Get-Date).ToUniversalTime().ToString("o")
        install_root = $InstallRoot
        database_restored = (-not $SkipDatabase)
        model_requested = (-not $SkipModel)
    }
    $installed | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $InstallRoot "install-manifest.json") -Encoding utf8
    Write-Step "安装完成。启动器：$InstallRoot\GF_FMR_Server.exe"
    if ($StartServer) { Start-Process -FilePath (Join-Path $InstallRoot "GF_FMR_Server.exe") -WorkingDirectory $InstallRoot }
} finally {
    Remove-Item -LiteralPath $downloadRoot -Recurse -Force -ErrorAction SilentlyContinue
}
