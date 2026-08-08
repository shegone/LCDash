#Requires -Version 5.1
#Requires -RunAsAdministrator

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$setupLog = Join-Path $PSScriptRoot "PC15_REMOTE_ACCESS_SETUP.log"
$publicKey = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIIG3j0NgAm44g2UwivSEe0j5wlCgeyxeuB6W2mKuEiyE lcdash-windows-to-serv1"
$firewallName = "LCDash PC15 OpenSSH from local network"

function Add-AuthorizedKey {
    param(
        [Parameter(Mandatory)]
        [string]$Path
    )

    $directory = Split-Path -Parent $Path
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }

    if (-not (Test-Path -LiteralPath $Path)) {
        New-Item -ItemType File -Path $Path -Force | Out-Null
    }

    $existingKeys = @(Get-Content -LiteralPath $Path -ErrorAction SilentlyContinue)
    if ($existingKeys -notcontains $publicKey) {
        Add-Content -LiteralPath $Path -Value $publicKey -Encoding ascii
    }
}

Start-Transcript -LiteralPath $setupLog -Force | Out-Null

try {
    Write-Host "Preparing PC .15 for managed LCDash agent access..." -ForegroundColor Cyan

    $openSsh = Get-WindowsCapability -Online |
        Where-Object Name -Like "OpenSSH.Server*" |
        Select-Object -First 1

    if (-not $openSsh) {
        throw "Windows did not report the OpenSSH Server optional capability."
    }

    if ($openSsh.State -ne "Installed") {
        Write-Host "Installing the Windows OpenSSH Server capability..."
        Add-WindowsCapability -Online -Name $openSsh.Name | Out-Null
    }

    Set-Service -Name sshd -StartupType Automatic
    Start-Service -Name sshd

    Add-AuthorizedKey -Path (Join-Path $env:USERPROFILE ".ssh\authorized_keys")

    $administratorKeys = Join-Path $env:ProgramData "ssh\administrators_authorized_keys"
    Add-AuthorizedKey -Path $administratorKeys
    & icacls.exe $administratorKeys /inheritance:r /grant:r "*S-1-5-32-544:F" "*S-1-5-18:F" | Out-Null
    if ($LASTEXITCODE -ne 0) {
        throw "Windows could not apply the required permissions to the administrator key file."
    }

    $existingRule = Get-NetFirewallRule -DisplayName $firewallName -ErrorAction SilentlyContinue
    if ($existingRule) {
        Set-NetFirewallRule -DisplayName $firewallName -Enabled True -Action Allow -Profile Any
        Set-NetFirewallAddressFilter -AssociatedNetFirewallRule $existingRule -RemoteAddress "14.1.1.0/24"
    }
    else {
        New-NetFirewallRule `
            -DisplayName $firewallName `
            -Direction Inbound `
            -Action Allow `
            -Protocol TCP `
            -LocalPort 22 `
            -RemoteAddress "14.1.1.0/24" `
            -Profile Any | Out-Null
    }

    Restart-Service -Name sshd

    $service = Get-Service -Name sshd
    $listener = Get-NetTCPConnection -State Listen -LocalPort 22 -ErrorAction SilentlyContinue
    if ($service.Status -ne "Running" -or -not $listener) {
        throw "OpenSSH was installed but did not start listening on TCP port 22."
    }

    Write-Host ""
    Write-Host "PC .15 remote management is ready." -ForegroundColor Green
    Write-Host "Computer name: $env:COMPUTERNAME"
    Write-Host "Windows account: $env:USERNAME"
    Write-Host "OpenSSH service: $($service.Status)"
    Write-Host "Listening port: 22"
    Write-Host "Allowed source network: 14.1.1.0/24"
    Write-Host "Log file: $setupLog"
}
catch {
    Write-Host ""
    Write-Host "Setup stopped: $($_.Exception.Message)" -ForegroundColor Red
    Write-Host "Log file: $setupLog"
    throw
}
finally {
    Stop-Transcript | Out-Null
}

Write-Host ""
Read-Host "Press Enter after you have told Codex that setup is complete"
