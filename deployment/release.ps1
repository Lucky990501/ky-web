param(
  [Parameter(Mandatory = $true)]
  [string]$ProjectRoot,
  [switch]$Execute
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
$secretFile = Join-Path $ProjectRoot '.deploy-secrets.local'
if (-not (Test-Path -LiteralPath $secretFile)) {
  throw "Local deploy config is missing: $secretFile. Copy .deploy-secrets.example to .deploy-secrets.local and fill it locally."
}

$config = @{}
Get-Content -LiteralPath $secretFile -Encoding UTF8 | ForEach-Object {
  $line = $_.Trim()
  if ($line -and -not $line.StartsWith('#') -and $line.Contains('=')) {
    $parts = $line -split '=', 2
    $config[$parts[0].Trim()] = $parts[1].Trim()
  }
}
function Get-ConfigValue([string]$Name, [string]$Fallback = '') {
  if ($config.ContainsKey($Name) -and -not [string]::IsNullOrWhiteSpace($config[$Name])) { return $config[$Name] }
  return $Fallback
}

$projectName = Get-ConfigValue 'PROJECT_NAME' (Split-Path $ProjectRoot -Leaf)
$serverHost = Get-ConfigValue 'SERVER_HOST'
$serverUser = Get-ConfigValue 'SERVER_USER'
$serverPort = Get-ConfigValue 'SERVER_PORT' '22'
$stageDir = Get-ConfigValue 'SERVER_STAGE_DIR' "/root/$projectName-bridge"
$keyPath = Get-ConfigValue 'SERVER_SSH_KEY_PATH'
$githubRemote = Get-ConfigValue 'GITHUB_REMOTE'
$branch = Get-ConfigValue 'GITHUB_BRANCH' 'main'
# SERVER_SITE_ROOT / SERVER_SITE_HOST remain supported for existing projects.
$releaseRoot = Get-ConfigValue 'SERVER_RELEASE_ROOT' (Get-ConfigValue 'SERVER_SITE_ROOT' "/var/www/$projectName")
$siteHost = Get-ConfigValue 'HEALTHCHECK_HOST' (Get-ConfigValue 'SERVER_SITE_HOST')
$healthUrl = Get-ConfigValue 'HEALTHCHECK_URL' $(if ($siteHost) { "https://$siteHost/" } else { '' })
$originHealthUrl = Get-ConfigValue 'ORIGIN_HEALTHCHECK_URL'
$originHealthHost = Get-ConfigValue 'ORIGIN_HEALTHCHECK_HOST' $siteHost

$required = @{
  SERVER_HOST = $serverHost; SERVER_USER = $serverUser; SERVER_PORT = $serverPort
  SERVER_SSH_KEY_PATH = $keyPath; GITHUB_REMOTE = $githubRemote; GITHUB_BRANCH = $branch
  SERVER_RELEASE_ROOT = $releaseRoot; HEALTHCHECK_URL = $healthUrl
}
$missing = @($required.GetEnumerator() | Where-Object { [string]::IsNullOrWhiteSpace($_.Value) } | ForEach-Object Key)
if ($missing) { throw "Deploy config is missing: $($missing -join ', ')" }

$commit = (git -C $ProjectRoot rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve the current Git commit.' }
foreach ($shellValue in @($projectName, $branch, $commit, $stageDir, $githubRemote, $releaseRoot, $siteHost, $healthUrl, $originHealthUrl, $originHealthHost)) {
  if ($shellValue -match "['`r`n]") { throw 'Deployment settings cannot contain quotes or line breaks.' }
}

$bundleDir = Join-Path $ProjectRoot '.deploy-bridge'
$bundleName = "$projectName-$commit.bundle"
$bundlePath = Join-Path $bundleDir $bundleName
$remote = "$serverUser@$serverHost"
$sshBase = @('-i', $keyPath, '-p', $serverPort)
$scpBase = @('-i', $keyPath, '-P', $serverPort)
Write-Host "Release: $projectName@$commit"
Write-Host "Route: local Git bundle -> ${remote}:$stageDir -> GitHub -> $healthUrl"

if (-not $Execute) {
  Write-Host 'Simulation only: no upload, Git push, or server deployment was performed.'
  exit 0
}
if ($keyPath -match 'path\\to|<') { throw 'Set SERVER_SSH_KEY_PATH in .deploy-secrets.local before executing.' }
if (-not (Test-Path -LiteralPath $keyPath)) { throw "SSH key file was not found: $keyPath" }

New-Item -ItemType Directory -Force -Path $bundleDir | Out-Null
git -C $ProjectRoot bundle create $bundlePath $branch
if ($LASTEXITCODE -ne 0) { throw 'Unable to create the Git bundle.' }
& ssh @sshBase $remote "mkdir -p '$stageDir'"
if ($LASTEXITCODE -ne 0) { throw 'Unable to create the server bridge directory.' }
& scp @scpBase $bundlePath "${remote}:$stageDir/"
if ($LASTEXITCODE -ne 0) { throw 'Unable to upload the Git bundle to the server.' }

$serverScript = @'
set -e
stage_dir='__STAGE_DIR__'
bundle_name='__BUNDLE_NAME__'
branch='__BRANCH__'
commit='__COMMIT__'
github_remote='__GITHUB_REMOTE__'
release_root='__RELEASE_ROOT__'
health_url='__HEALTH_URL__'
health_host='__HEALTH_HOST__'
origin_health_url='__ORIGIN_HEALTH_URL__'
origin_health_host='__ORIGIN_HEALTH_HOST__'
repo="$stage_dir/__PROJECT_NAME__.git"
release="$release_root/releases/$commit"
previous_release="$(readlink -f "$release_root/current" 2>/dev/null || true)"

mkdir -p "$stage_dir" "$release_root/releases" "$release"
git init --bare "$repo" >/dev/null 2>&1 || true
git --git-dir="$repo" fetch "$stage_dir/$bundle_name" "$branch:refs/heads/$branch"
git --git-dir="$repo" push "$github_remote" "refs/heads/$branch:refs/heads/$branch"
git --work-tree="$release" --git-dir="$repo" checkout -f "$branch" -- .
find "$release_root" -type d -exec chmod 755 {} \;
find "$release_root" -type f -exec chmod 644 {} \;
ln -sfn "$release" "$release_root/current"

rollback() {
  if [ -n "$previous_release" ] && [ -d "$previous_release" ]; then
    ln -sfn "$previous_release" "$release_root/current"
    nginx -t >/dev/null && systemctl reload nginx || true
  fi
}

if ! nginx -t; then rollback; exit 1; fi
systemctl reload nginx
if [ -n "$origin_health_url" ]; then
  if ! status="$(curl -k -sS -o /dev/null -w '%{http_code}' -H "Host: $origin_health_host" "$origin_health_url")"; then
    rollback
    echo 'Origin health check connection failed.' >&2
    exit 1
  fi
elif [ -n "$health_host" ]; then
  if ! status="$(curl -k -sS -o /dev/null -w '%{http_code}' --resolve "$health_host:443:127.0.0.1" "$health_url")"; then
    rollback
    echo 'Health check connection failed.' >&2
    exit 1
  fi
else
  if ! status="$(curl -k -sS -o /dev/null -w '%{http_code}' "$health_url")"; then
    rollback
    echo 'Health check connection failed.' >&2
    exit 1
  fi
fi
if [ "$status" != "200" ]; then
  rollback
  echo "Website health check failed with HTTP $status" >&2
  exit 1
fi
echo "Published commit $commit and deployed $health_url (HTTP $status)"
'@

$values = @{
  '__STAGE_DIR__' = $stageDir; '__BUNDLE_NAME__' = $bundleName; '__BRANCH__' = $branch
  '__COMMIT__' = $commit; '__GITHUB_REMOTE__' = $githubRemote; '__RELEASE_ROOT__' = $releaseRoot
  '__HEALTH_URL__' = $healthUrl; '__HEALTH_HOST__' = $siteHost; '__ORIGIN_HEALTH_URL__' = $originHealthUrl
  '__ORIGIN_HEALTH_HOST__' = $originHealthHost; '__PROJECT_NAME__' = $projectName
}
foreach ($token in $values.Keys) { $serverScript = $serverScript.Replace($token, $values[$token]) }
$encodedServerScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($serverScript))
& ssh @sshBase $remote "echo $encodedServerScript | base64 -d | bash"
if ($LASTEXITCODE -ne 0) { throw 'Publishing or website deployment failed. The prior release was restored if the health check failed.' }
Write-Host "Complete: $commit was pushed and deployed to $healthUrl"
