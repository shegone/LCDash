#Requires -Version 5.1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$agentRoot = "C:\MAE-Agent"
$logDirectory = Join-Path $agentRoot "logs"
$computerExecutable = Join-Path $agentRoot "cptr-venv\Scripts\cptr.exe"
$nodePackageRoot = Join-Path $env:LOCALAPPDATA "Microsoft\WinGet\Packages"
$nodeDirectory = Get-ChildItem -LiteralPath $nodePackageRoot -Directory -ErrorAction SilentlyContinue |
    Where-Object Name -Like "OpenJS.NodeJS.LTS_*" |
    ForEach-Object {
        Get-ChildItem -LiteralPath $_.FullName -Directory -Filter "node-*-win-x64" -ErrorAction SilentlyContinue
    } |
    Select-Object -First 1 -ExpandProperty FullName

if (-not (Test-Path -LiteralPath $computerExecutable)) {
    throw "Open WebUI Computer is not installed at $computerExecutable"
}

New-Item -ItemType Directory -Path $logDirectory -Force | Out-Null

# Logon tasks can overlap with a manually started Computer instance. Treat an
# already healthy listener as success instead of launching a second process
# that would fail because port 8000 is occupied.
try {
    $health = Invoke-RestMethod -Uri "http://127.0.0.1:8000/api/health" -TimeoutSec 3
    if ($health.status -eq "ok") {
        $timestamp = Get-Date -Format "yyyy-MM-ddTHH:mm:ssK"
        Add-Content -LiteralPath (Join-Path $logDirectory "computer-autostart.log") `
            -Value "$timestamp Computer is already healthy; startup skipped."
        exit 0
    }
} catch {
    # No healthy local listener exists, so continue with normal startup.
}

$pathParts = @(
    "C:\Program Files\Git\cmd"
    $nodeDirectory
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312")
    (Join-Path $env:LOCALAPPDATA "Programs\Python\Python312\Scripts")
    $env:Path
) | Where-Object { $_ }
$env:Path = $pathParts -join ";"

Set-Location -LiteralPath "C:\project mae share"

$stdoutLog = Join-Path $logDirectory "computer-output.log"
$stderrLog = Join-Path $logDirectory "computer-error.log"

& $computerExecutable run --host 0.0.0.0 --port 8000 --headless 1>> $stdoutLog 2>> $stderrLog
