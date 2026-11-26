param([int]$WebPort = 5173)

$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Run this script from an elevated Administrator PowerShell window.'
}

$ruleName = "GF FMR Workbench TCP $WebPort"
if (-not (Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue)) {
    New-NetFirewallRule -DisplayName $ruleName -Direction Inbound -Action Allow `
        -Protocol TCP -LocalPort $WebPort -Profile Any -RemoteAddress LocalSubnet | Out-Null
    Write-Host "Firewall rule enabled for LocalSubnet on TCP $WebPort."
} else {
    Set-NetFirewallRule -DisplayName $ruleName -Profile Any -Enabled True -Action Allow -Direction Inbound
    Write-Host "Firewall rule already exists: $ruleName"
}
