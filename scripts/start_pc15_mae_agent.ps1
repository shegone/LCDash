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
