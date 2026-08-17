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
$siteRoot = if ($deployConfig.ContainsKey('SERVER_SITE_ROOT') -and -not [string]::IsNullOrWhiteSpace($deployConfig.SERVER_SITE_ROOT)) { $deployConfig.SERVER_SITE_ROOT } else { '/var/www/kunyuan-ai' }
$siteHost = if ($deployConfig.ContainsKey('SERVER_SITE_HOST') -and -not [string]::IsNullOrWhiteSpace($deployConfig.SERVER_SITE_HOST)) { $deployConfig.SERVER_SITE_HOST } else { 'luckio.cn' }

foreach ($shellValue in @($branch, $commit, $deployConfig.SERVER_STAGE_DIR, $deployConfig.GITHUB_REMOTE, $siteRoot, $siteHost)) {
  if ($shellValue -match "['`r`n]") { throw 'Deployment settings cannot contain quotes or line breaks.' }
}

Write-Host "Bridge commit: $commit"
Write-Host "Route: local Git bundle -> ${remote}:$($deployConfig.SERVER_STAGE_DIR) -> GitHub -> https://$siteHost"

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

$serverScript = @'
set -e

stage_dir='__STAGE_DIR__'
bundle_name='__BUNDLE_NAME__'
branch='__BRANCH__'
commit='__COMMIT__'
github_remote='__GITHUB_REMOTE__'
site_root='__SITE_ROOT__'
site_host='__SITE_HOST__'
repo="$stage_dir/ky-web.git"
release="$site_root/releases/$commit"
previous_release="$(readlink -f "$site_root/current" 2>/dev/null || true)"

mkdir -p "$stage_dir" "$site_root/releases" "$release"
git init --bare "$repo" >/dev/null 2>&1 || true
git --git-dir="$repo" fetch "$stage_dir/$bundle_name" "$branch:refs/heads/$branch"
git --git-dir="$repo" push "$github_remote" "refs/heads/$branch:refs/heads/$branch"
git --work-tree="$release" --git-dir="$repo" checkout -f "$branch" -- .

find "$site_root" -type d -exec chmod 755 {} \;
find "$site_root" -type f -exec chmod 644 {} \;
ln -sfn "$release" "$site_root/current"

rollback() {
  if [ -n "$previous_release" ] && [ -d "$previous_release" ]; then
    ln -sfn "$previous_release" "$site_root/current"
    nginx -t >/dev/null && systemctl reload nginx || true
  fi
}

if ! nginx -t; then
  rollback
  exit 1
fi
systemctl reload nginx

status="$(curl -k -sS -o /dev/null -w '%{http_code}' --resolve "$site_host:443:127.0.0.1" "https://$site_host/")"
if [ "$status" != "200" ]; then
  rollback
  echo "Website health check failed with HTTP $status" >&2
  exit 1
fi

echo "Published commit $commit and deployed https://$site_host (HTTP $status)"
'@

$serverScript = $serverScript.Replace('__STAGE_DIR__', $deployConfig.SERVER_STAGE_DIR)
$serverScript = $serverScript.Replace('__BUNDLE_NAME__', (Split-Path $bundlePath -Leaf))
$serverScript = $serverScript.Replace('__BRANCH__', $branch)
$serverScript = $serverScript.Replace('__COMMIT__', $commit)
$serverScript = $serverScript.Replace('__GITHUB_REMOTE__', $deployConfig.GITHUB_REMOTE)
$serverScript = $serverScript.Replace('__SITE_ROOT__', $siteRoot)
$serverScript = $serverScript.Replace('__SITE_HOST__', $siteHost)
$encodedServerScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($serverScript))
$serverCommand = "echo $encodedServerScript | base64 -d | bash"
& ssh @sshBase $remote $serverCommand
if ($LASTEXITCODE -ne 0) { throw 'Publishing or website deployment failed. The prior release was restored if the health check failed.' }

Write-Host "Complete: $commit was pushed and deployed to https://$siteHost"
