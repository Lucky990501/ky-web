param([switch]$Execute)

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$secretFile = Join-Path $projectRoot '.deploy-secrets.local'

if (-not (Test-Path -LiteralPath $secretFile)) {
  throw "Local deploy config is missing: $secretFile"
}

$deployConfig = @{}
Get-Content -LiteralPath $secretFile -Encoding UTF8 | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
    $parts = $line -split '=', 2
    $deployConfig[$parts[0].Trim()] = $parts[1].Trim()
  }
}

$required = 'SERVER_HOST', 'SERVER_USER', 'SERVER_PORT', 'SERVER_STAGE_DIR', 'SERVER_SSH_KEY_PATH', 'GITHUB_REMOTE', 'GITHUB_BRANCH'
$missing = @()
foreach ($settingName in $required) {
  if (-not $deployConfig.ContainsKey($settingName) -or [string]::IsNullOrWhiteSpace($deployConfig[$settingName])) {
    $missing += $settingName
  }
}
if ($missing) { throw "Deploy config is missing: $($missing -join ', ')" }

$branch = $deployConfig.GITHUB_BRANCH
$commit = (git -C $projectRoot rev-parse --verify HEAD).Trim()
$bundleDir = Join-Path $projectRoot '.deploy-bridge'
$bundlePath = Join-Path $bundleDir "ky-web-$commit.bundle"
$remote = "$($deployConfig.SERVER_USER)@$($deployConfig.SERVER_HOST)"
$sshBase = @('-i', $deployConfig.SERVER_SSH_KEY_PATH, '-p', $deployConfig.SERVER_PORT)
$scpBase = @('-i', $deployConfig.SERVER_SSH_KEY_PATH, '-P', $deployConfig.SERVER_PORT)

Write-Host "Bridge commit: $commit"
Write-Host "Route: local Git bundle -> ${remote}:$($deployConfig.SERVER_STAGE_DIR) -> GitHub"

if (-not $Execute) {
  Write-Host 'Simulation only: no connection, upload, or push was performed. Run .\publish-via-server.ps1 -Execute when ready.'
  if ($deployConfig.SERVER_SSH_KEY_PATH -match 'path\\to|<') { Write-Host 'Action required: set SERVER_SSH_KEY_PATH in .deploy-secrets.local.' }
  exit 0
}
if ($deployConfig.SERVER_SSH_KEY_PATH -match 'path\\to|<') { throw 'Set SERVER_SSH_KEY_PATH in .deploy-secrets.local before executing.' }

New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
git -C $projectRoot bundle create $bundlePath $branch
if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Git bundle.' }

& ssh @sshBase $remote "mkdir -p '$($deployConfig.SERVER_STAGE_DIR)'"
if ($LASTEXITCODE -ne 0) { throw 'Unable to create the server bridge directory.' }
& scp @scpBase $bundlePath "${remote}:$($deployConfig.SERVER_STAGE_DIR)/"
if ($LASTEXITCODE -ne 0) { throw 'Unable to upload the Git bundle to the server.' }

$serverCommand = "set -e; cd '$($deployConfig.SERVER_STAGE_DIR)'; git init --bare ky-web.git >/dev/null 2>&1 || true; git --git-dir=ky-web.git fetch '$($bundlePath | Split-Path -Leaf)' '$branch':refs/heads/'$branch'; git --git-dir=ky-web.git push '$($deployConfig.GITHUB_REMOTE)' refs/heads/'$branch':refs/heads/'$branch'"
& ssh @sshBase $remote $serverCommand
if ($LASTEXITCODE -ne 0) { throw 'The server could not push to GitHub. Check GitHub SSH authentication on the server.' }

Write-Host "Complete: $commit was pushed through the server."
