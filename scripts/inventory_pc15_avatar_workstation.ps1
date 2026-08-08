#Requires -Version 5.1

[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$project = "C:\Users\admin\UnrealProjects\MAE_Avatar_Baseline 5.8"

Write-Output "=== SYSTEM ==="
Get-CimInstance Win32_OperatingSystem | ForEach-Object {
    Write-Output ("OS|" + $_.Caption + "|" + $_.Version + "|LastBoot=" + $_.LastBootUpTime.ToString("s"))
}
Get-CimInstance Win32_VideoController | Where-Object Name -Match "NVIDIA" | ForEach-Object {
    Write-Output ("GPU|" + $_.Name + "|Driver=" + $_.DriverVersion + "|RAM=" + [math]::Round($_.AdapterRAM / 1GB, 1) + "GB")
}
Get-PSDrive -PSProvider FileSystem | ForEach-Object {
    Write-Output ("DISK|" + $_.Name + "|FreeGB=" + [math]::Round($_.Free / 1GB, 1) + "|UsedGB=" + [math]::Round($_.Used / 1GB, 1))
}

Write-Output "=== INSTALLED CREATIVE SOFTWARE ==="
$uninstall = @(
    "HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
    "HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*"
    "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*"
)
Get-ItemProperty $uninstall -ErrorAction SilentlyContinue |
    Where-Object DisplayName -Match "iClone|Character Creator|Unreal Engine|Reallusion|Live Link|MetaHuman" |
    Sort-Object DisplayName -Unique |
    ForEach-Object {
        Write-Output ("APP|" + $_.DisplayName + "|Version=" + $_.DisplayVersion + "|Path=" + $_.InstallLocation)
    }

Write-Output "=== PROJECT ==="
Write-Output ("PROJECT|" + $project + "|Exists=" + (Test-Path -LiteralPath $project))
Get-Content -LiteralPath (Join-Path $project "MAE_Avatar_Baseline.uproject") -ErrorAction SilentlyContinue

$content = Join-Path $project "Content"
Get-ChildItem -LiteralPath $content -Directory -ErrorAction SilentlyContinue |
    Sort-Object Name |
    ForEach-Object {
        $count = (Get-ChildItem -LiteralPath $_.FullName -Recurse -File -ErrorAction SilentlyContinue | Measure-Object).Count
        Write-Output ("CONTENT|" + $_.Name + "|Files=" + $count)
    }
$totalAssets = (Get-ChildItem -LiteralPath $content -Recurse -File -Filter *.uasset -ErrorAction SilentlyContinue | Measure-Object).Count
$totalMaps = (Get-ChildItem -LiteralPath $content -Recurse -File -Filter *.umap -ErrorAction SilentlyContinue | Measure-Object).Count
Write-Output ("ASSETS|uasset=" + $totalAssets + "|umap=" + $totalMaps)

Write-Output "=== PROJECT PLUGINS ==="
$projectPlugins = Join-Path $project "Plugins"
if (Test-Path -LiteralPath $projectPlugins) {
    Get-ChildItem -LiteralPath $projectPlugins -Directory | ForEach-Object {
        Write-Output ("PROJECT_PLUGIN|" + $_.Name)
    }
} else {
    Write-Output "PROJECT_PLUGIN|none"
}

Write-Output "=== REALLUSION CONTENT ROOTS ==="
@(
    "C:\Users\Public\Documents\Reallusion"
    "C:\Users\admin\Documents\Reallusion"
    "C:\Users\Public\Documents\Reallusion Custom"
    "C:\Users\admin\Documents\Reallusion Custom"
) | ForEach-Object {
    if (Test-Path -LiteralPath $_) {
        $top = (Get-ChildItem -LiteralPath $_ -Directory -ErrorAction SilentlyContinue | Select-Object -ExpandProperty Name) -join ","
        Write-Output ("RL_ROOT|" + $_ + "|Top=" + $top)
    }
}

Write-Output "=== RUNNING WINDOWS ==="
Get-Process | Where-Object ProcessName -Match "iClone|UnrealEditor|CharacterCreator" | ForEach-Object {
    Write-Output ("RUNNING|" + $_.ProcessName + "|PID=" + $_.Id + "|Title=" + $_.MainWindowTitle)
}
