param(
    [int]$WebPort = 5173,
    [int]$ApiPort = 8000,
    [switch]$OpenBrowser,
    [switch]$AllowFirewall
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path
$StateRoot = Join-Path $ProjectRoot 'state'
$Python = Join-Path $StateRoot 'dependencies\venv\Scripts\python.exe'
$Node = Join-Path $StateRoot 'dependencies\node22\node.exe'
$Vite = Join-Path $ProjectRoot 'platform\web\node_modules\vite\bin\vite.js'
$PasswordFile = Join-Path $StateRoot 'runtime\postgres-superuser.txt'
$ApiLog = Join-Path $StateRoot 'runtime\api-lan.log'
$ApiErr = Join-Path $StateRoot 'runtime\api-lan.err.log'
$WebLog = Join-Path $StateRoot 'runtime\web-lan.log'
$WebErr = Join-Path $StateRoot 'runtime\web-lan.err.log'

foreach ($path in @($Python, $Node, $Vite, $PasswordFile)) {
    if (-not (Test-Path -LiteralPath $path)) {
        throw "Required runtime file not found: $path"
    }
}

$password = (Get-Content -LiteralPath $PasswordFile -Raw).Trim()
$env:FACTSHEET_PROJECT_ROOT = $ProjectRoot
$env:FACTSHEET_STATE_ROOT = $StateRoot
$env:FACTSHEET_DATA_ROOT = Join-Path $StateRoot 'data'
$env:FACTSHEET_RUNTIME_ROOT = Join-Path $StateRoot 'runtime'
$env:PLATFORM_RUNTIME_ROOT = Join-Path $StateRoot 'platform-runtime'
$env:FACTSHEET_CONFIG = Join-Path $StateRoot 'config.ini'
$env:PYTHONPATH = "$ProjectRoot\platform\api;$ProjectRoot\code"
$env:PLATFORM_DATABASE_URL = "postgresql+psycopg://postgres:$password@127.0.0.1:5432/fund_research"
$env:PLATFORM_API_HOST = '127.0.0.1'
$env:PLATFORM_API_PORT = "$ApiPort"
$env:PLATFORM_WEB_HOST = '0.0.0.0'
$env:PLATFORM_WEB_PORT = "$WebPort"
$env:PLATFORM_API_PROXY_TARGET = "http://127.0.0.1:$ApiPort"

New-Item -ItemType Directory -Force -Path (Join-Path $StateRoot 'runtime') | Out-Null

try {
    $health = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2
    Write-Host "API already running: http://127.0.0.1:$ApiPort"
} catch {
    Start-Process -FilePath $Python `
        -ArgumentList '-m','uvicorn','app.main:app','--host','127.0.0.1','--port',"$ApiPort" `
        -WorkingDirectory $ProjectRoot `
        -RedirectStandardOutput $ApiLog `
        -RedirectStandardError $ApiErr | Out-Null
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$ApiPort/api/health" -TimeoutSec 2 | Out-Null; break } catch { }
        if ($i -eq 39) { throw "API did not become ready; see $ApiErr" }
    }
    Write-Host "API started on 127.0.0.1:$ApiPort"
}

try {
    Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$WebPort/" -TimeoutSec 2 | Out-Null
    Write-Host "Web already running on port $WebPort"
} catch {
    Start-Process -FilePath $Node `
        -ArgumentList $Vite,'--host','0.0.0.0','--port',"$WebPort" `
        -WorkingDirectory (Join-Path $ProjectRoot 'platform\web') `
        -RedirectStandardOutput $WebLog `
        -RedirectStandardError $WebErr | Out-Null
    for ($i = 0; $i -lt 40; $i++) {
        Start-Sleep -Milliseconds 250
        try { Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:$WebPort/" -TimeoutSec 2 | Out-Null; break } catch { }
        if ($i -eq 39) { throw "Web did not become ready; see $WebErr" }
    }
    Write-Host "Web started on 0.0.0.0:$WebPort"
}

if ($AllowFirewall) {
    $ruleName = "GF FMR Workbench TCP $WebPort"
    $existing = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    if (-not $existing) {
        New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
            -Protocol TCP -LocalPort $WebPort -Profile Any -RemoteAddress LocalSubnet | Out-Null
        Write-Host "Firewall rule created for LocalSubnet on TCP $WebPort"
    } else {
        Set-NetFirewallRule -DisplayName $ruleName -Profile Any -Enabled True -Action Allow -Direction Inbound
        Write-Host "Firewall rule already exists: $ruleName"
    }
}

$addresses = Get-NetIPAddress -AddressFamily IPv4 -AddressState Preferred -ErrorAction SilentlyContinue |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object -ExpandProperty IPAddress
if (-not $addresses) {
    $addresses = [System.Net.Dns]::GetHostEntry([System.Net.Dns]::GetHostName()).AddressList |
        Where-Object { $_.AddressFamily -eq [System.Net.Sockets.AddressFamily]::InterNetwork } |
        ForEach-Object { $_.IPAddressToString } |
        Where-Object { $_ -notlike '127.*' -and $_ -notlike '169.254.*' }
}
if (-not $addresses) { $addresses = @('127.0.0.1') }
foreach ($address in $addresses) {
    Write-Host "Workbench: http://$address`:$WebPort/"
}
if ($OpenBrowser) {
    Start-Process "http://$($addresses[0]):$WebPort/"
}
