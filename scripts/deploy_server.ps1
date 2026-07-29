$ErrorActionPreference = "Stop"

$ProjectPath = "E:\Projects\LCDash"
$Server = "administrator@14.1.1.227"
$SshKey = Join-Path $env:USERPROFILE ".ssh\lcdash_server_ed25519"
$RemoteArchive = "/srv/lcdash-platform/incoming/incoming-lcdash.tar.gz"
$RemoteDeployScript = "/srv/lcdash-platform/bin/deploy-lcdash.sh"
$LocalArchive = Join-Path $env:TEMP "lcdash-deploy.tar.gz"
$ExpectedBranch = "deployment/ubuntu-nvidia-227"

Set-Location $ProjectPath

if (-not (Test-Path -LiteralPath $SshKey)) {
    throw "LCDash server SSH key was not found: $SshKey"
}

$branch = (git branch --show-current).Trim()
if ($branch -ne $ExpectedBranch) {
    throw "Expected $ExpectedBranch, but the current branch is $branch."
}

$workingChanges = git status --porcelain
if ($workingChanges) {
    throw "The project has uncommitted changes. Commit and push them in GitHub Desktop first."
}

git fetch origin $ExpectedBranch
if ($LASTEXITCODE -ne 0) {
    throw "GitHub could not be checked."
}

$localCommit = (git rev-parse HEAD).Trim()
$remoteCommit = (git rev-parse "origin/$ExpectedBranch").Trim()
if ($localCommit -ne $remoteCommit) {
    throw "Windows and GitHub are not synchronized. Use GitHub Desktop to push or pull first."
}

if (Test-Path -LiteralPath $LocalArchive) {
    Remove-Item -LiteralPath $LocalArchive -Force
}

try {
    git archive --format=tar.gz --output=$LocalArchive HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "LCDash could not be packaged."
    }

    scp -i $SshKey $LocalArchive "${Server}:$RemoteArchive"
    if ($LASTEXITCODE -ne 0) {
        throw "LCDash could not be uploaded to the server."
    }

    ssh -i $SshKey $Server "$RemoteDeployScript $RemoteArchive"
    if ($LASTEXITCODE -ne 0) {
        throw "The server rejected the deployment or rolled it back."
    }

    Write-Host ""
    Write-Host "LCDash deployment completed successfully." -ForegroundColor Green
    Write-Host "Dashboard: http://127.0.0.1:8010/dashboard"
    Write-Host "Local AI:  http://127.0.0.1:3000"
}
finally {
    if (Test-Path -LiteralPath $LocalArchive) {
        Remove-Item -LiteralPath $LocalArchive -Force
    }
}
