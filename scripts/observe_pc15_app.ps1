#Requires -Version 5.1

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$App,

    [Parameter(Mandatory = $true)]
    [string]$OutputPath
)

$ErrorActionPreference = "Stop"
$node = "C:\MAE-Agent\runtime\node.exe"
$openComputerUse = "C:\MAE-Agent\npm\node_modules\open-computer-use\bin\open-computer-use"

$arguments = @{
    app = $App
    text_limit = 500
    max_tree_nodes = 500
    max_tree_depth = 30
} | ConvertTo-Json -Compress

$argumentFile = "$OutputPath.arguments.json"
$arguments | Set-Content -LiteralPath $argumentFile -Encoding ASCII
$previousErrorActionPreference = $ErrorActionPreference
$ErrorActionPreference = "Continue"
$result = (& $node $openComputerUse call get_app_state --args-file $argumentFile 2>&1) -join "`n"
$nativeExitCode = $LASTEXITCODE
$ErrorActionPreference = $previousErrorActionPreference
Remove-Item -LiteralPath $argumentFile -Force -ErrorAction SilentlyContinue
if ($nativeExitCode -ne 0) {
    $result | Set-Content -LiteralPath "$OutputPath.error.txt" -Encoding UTF8
    throw "Open Computer Use returned exit code $nativeExitCode; details saved beside output"
}

try {
    $parsed = $result | ConvertFrom-Json
} catch {
    $result | Set-Content -LiteralPath "$OutputPath.error.txt" -Encoding UTF8
    throw
}
$text = $parsed.content |
    Where-Object type -EQ "text" |
    Select-Object -ExpandProperty text
$text | Set-Content -LiteralPath $OutputPath -Encoding UTF8
