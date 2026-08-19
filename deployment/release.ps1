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
$nginxSiteName = Get-ConfigValue 'NGINX_SITE_NAME' $projectName
$nginxServerName = Get-ConfigValue 'NGINX_SERVER_NAME' $siteHost
$nginxCertificate = Get-ConfigValue 'NGINX_TLS_CERTIFICATE'
$nginxCertificateKey = Get-ConfigValue 'NGINX_TLS_CERTIFICATE_KEY'
$nginxSslOptions = Get-ConfigValue 'NGINX_SSL_OPTIONS' '/etc/letsencrypt/options-ssl-nginx.conf'
$nginxDhParam = Get-ConfigValue 'NGINX_SSL_DHPARAM' '/etc/letsencrypt/ssl-dhparams.pem'

$required = @{
  SERVER_HOST = $serverHost; SERVER_USER = $serverUser; SERVER_PORT = $serverPort
  SERVER_SSH_KEY_PATH = $keyPath; GITHUB_REMOTE = $githubRemote; GITHUB_BRANCH = $branch
  SERVER_RELEASE_ROOT = $releaseRoot; HEALTHCHECK_URL = $healthUrl; NGINX_SITE_NAME = $nginxSiteName
  NGINX_SERVER_NAME = $nginxServerName; NGINX_TLS_CERTIFICATE = $nginxCertificate
  NGINX_TLS_CERTIFICATE_KEY = $nginxCertificateKey
}
$missing = @($required.GetEnumerator() | Where-Object { [string]::IsNullOrWhiteSpace($_.Value) } | ForEach-Object Key)
if ($missing) { throw "Deploy config is missing: $($missing -join ', ')" }

$commit = (git -C $ProjectRoot rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0) { throw 'Unable to resolve the current Git commit.' }
foreach ($shellValue in @($projectName, $branch, $commit, $stageDir, $githubRemote, $releaseRoot, $siteHost, $healthUrl, $originHealthUrl, $originHealthHost)) {
  if ($shellValue -match "['`r`n]") { throw 'Deployment settings cannot contain quotes or line breaks.' }
}
if ($nginxSiteName -notmatch '^[A-Za-z0-9_-]+$') { throw 'NGINX_SITE_NAME may contain only letters, numbers, hyphens, and underscores.' }
if ($nginxServerName -notmatch '^[A-Za-z0-9.-]+( [A-Za-z0-9.-]+)*$') { throw 'NGINX_SERVER_NAME must contain valid domain names separated by spaces.' }
foreach ($nginxPath in @($nginxCertificate, $nginxCertificateKey, $nginxSslOptions, $nginxDhParam)) {
  if ($nginxPath -notmatch '^/[A-Za-z0-9._/-]+$') { throw 'Nginx file paths must be absolute paths without special characters.' }
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

$nginxConfig = @'
server {
    listen 80;
    server_name __NGINX_SERVER_NAME__;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name __NGINX_SERVER_NAME__;
    root __RELEASE_ROOT__/current;
    index index.html;

    ssl_certificate __NGINX_CERTIFICATE__;
    ssl_certificate_key __NGINX_CERTIFICATE_KEY__;
    include __NGINX_SSL_OPTIONS__;
    ssl_dhparam __NGINX_DHPARAM__;

    location / { try_files $uri $uri/ /index.html; }

    location ^~ /api/ {
        client_max_body_size 64k;
        proxy_pass http://127.0.0.1:18780;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
    location ^~ /api/auth/ {
        client_max_body_size 32k;
        proxy_pass http://127.0.0.1:18780;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location = /admin { return 301 /admin/; }
    location ^~ /admin/ {
        auth_basic "Kunyuan AI Admin";
        auth_basic_user_file /etc/nginx/.htpasswd-kunyuan-admin;
        proxy_pass http://127.0.0.1:18780;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location ~* \.(?:css|js|png|jpg|jpeg|gif|svg|ico|webp|woff2?) {
        expires 7d;
        add_header Cache-Control "public";
        try_files $uri =404;
    }
}
'@
$nginxConfig = $nginxConfig.Replace('__NGINX_SERVER_NAME__', $nginxServerName)
$nginxConfig = $nginxConfig.Replace('__RELEASE_ROOT__', $releaseRoot)
$nginxConfig = $nginxConfig.Replace('__NGINX_CERTIFICATE__', $nginxCertificate)
$nginxConfig = $nginxConfig.Replace('__NGINX_CERTIFICATE_KEY__', $nginxCertificateKey)
$nginxConfig = $nginxConfig.Replace('__NGINX_SSL_OPTIONS__', $nginxSslOptions)
$nginxConfig = $nginxConfig.Replace('__NGINX_DHPARAM__', $nginxDhParam)
$encodedNginxConfig = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($nginxConfig))

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
nginx_site_name='__NGINX_SITE_NAME__'
nginx_config_b64='__NGINX_CONFIG_B64__'
repo="$stage_dir/__PROJECT_NAME__.git"
release="$release_root/releases/$commit"
previous_release="$(readlink -f "$release_root/current" 2>/dev/null || true)"
nginx_config="/etc/nginx/sites-available/$nginx_site_name.conf"
nginx_enabled="/etc/nginx/sites-enabled/$nginx_site_name.conf"
nginx_backup=''
admin_auth_file='/etc/nginx/.htpasswd-kunyuan-admin'
admin_initial_password='/root/.kunyuan-admin-initial-password'

mkdir -p "$stage_dir" "$release_root/releases" "$release"
git init --bare "$repo" >/dev/null 2>&1 || true
git --git-dir="$repo" fetch "$stage_dir/$bundle_name" "$branch:refs/heads/$branch"
git --git-dir="$repo" push "$github_remote" "refs/heads/$branch:refs/heads/$branch"
git --work-tree="$release" --git-dir="$repo" checkout -f "$branch" -- .
find "$release_root" -type d -exec chmod 755 {} \;
find "$release_root" -type f -exec chmod 644 {} \;
ln -sfn "$release" "$release_root/current"

if [ ! -f "$admin_auth_file" ]; then
  admin_password="$(openssl rand -base64 36 | tr -dc 'A-Za-z0-9' | head -c 24)"
  printf 'admin:%s\n' "$(openssl passwd -6 "$admin_password")" > "$admin_auth_file"
  printf '%s\n' "$admin_password" > "$admin_initial_password"
  chmod 600 "$admin_initial_password"
fi
chown root:www-data "$admin_auth_file"
chmod 640 "$admin_auth_file"

if [ -f "$release/admin_backend/server.py" ]; then
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y -qq postgresql python3-psycopg2
  mkdir -p /var/lib/kunyuan-admin
  install -d -m 700 /etc/kunyuan-admin
  database_env='/etc/kunyuan-admin/database.env'
  if [ ! -f "$database_env" ]; then
    database_password="$(openssl rand -base64 36 | tr -dc 'A-Za-z0-9' | head -c 32)"
    runuser -u postgres -- psql -v ON_ERROR_STOP=1 -c "CREATE USER kunyuan_app WITH LOGIN PASSWORD '$database_password';" || true
    runuser -u postgres -- psql -tAc "SELECT 1 FROM pg_database WHERE datname='kunyuan'" | grep -q 1 || runuser -u postgres -- createdb -O kunyuan_app kunyuan
    printf 'KUNYUAN_DATABASE_URL=postgresql://kunyuan_app:%s@127.0.0.1:5432/kunyuan\n' "$database_password" > "$database_env"
    chmod 600 "$database_env"
  fi
  cat > /etc/systemd/system/kunyuan-admin.service <<'SERVICE'
[Unit]
Description=Kunyuan AI administration service
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=__RELEASE_ROOT__/current/admin_backend
Environment=KUNYUAN_ADMIN_DB=/var/lib/kunyuan-admin/admin.db
EnvironmentFile=/etc/kunyuan-admin/database.env
Environment=KUNYUAN_LEGACY_SQLITE_DB=/var/lib/kunyuan-admin/admin.db
Environment=KUNYUAN_MIGRATE_LEGACY_SQLITE=1
Environment=KUNYUAN_DATABASE_MIGRATION_MARKER=/var/lib/kunyuan-admin/postgres-migration.done
Environment=KUNYUAN_ADMIN_DEPLOY_SCRIPT=/usr/local/sbin/kunyuan-admin-deploy
Environment=KUNYUAN_ADMIN_PORT=18780
ExecStart=/usr/bin/python3 __RELEASE_ROOT__/current/admin_backend/server.py
Restart=always
RestartSec=3
PrivateTmp=true
NoNewPrivileges=true

[Install]
WantedBy=multi-user.target
SERVICE
  cat > /usr/local/sbin/kunyuan-admin-deploy <<'ADMIN_DEPLOY'
#!/usr/bin/env bash
set -euo pipefail
repo='__STAGE_DIR__/__PROJECT_NAME__.git'
release_root='__RELEASE_ROOT__'
branch='__BRANCH__'
commit="$(git --git-dir="$repo" rev-parse "refs/heads/$branch")"
release="$release_root/releases/$commit"
mkdir -p "$release"
git --work-tree="$release" --git-dir="$repo" checkout -f "$branch" -- .
find "$release_root" -type d -exec chmod 755 {} \;
find "$release_root" -type f -exec chmod 644 {} \;
ln -sfn "$release" "$release_root/current"
systemctl restart kunyuan-admin
nginx -t
systemctl reload nginx
curl -k -sS -o /dev/null -w 'HTTP %{http_code}\n' -H 'Host: __HEALTH_HOST__' https://127.0.0.1/
ADMIN_DEPLOY
  chmod 700 /usr/local/sbin/kunyuan-admin-deploy
  systemctl daemon-reload
  systemctl enable kunyuan-admin >/dev/null
  systemctl restart kunyuan-admin
  if ! systemctl is-active --quiet kunyuan-admin; then
    echo 'Admin service did not start.' >&2
    exit 1
  fi
  if ! admin_status="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 15 http://127.0.0.1:18780/api/site-content)"; then
    echo 'Admin API health check connection failed.' >&2
    exit 1
  fi
  if [ "$admin_status" != "200" ]; then
    echo "Admin API health check failed with HTTP $admin_status" >&2
    exit 1
  fi
fi

restore_nginx_config() {
  if [ -n "$nginx_backup" ] && [ -f "$nginx_backup" ]; then
    cp -a "$nginx_backup" "$nginx_config"
  else
    rm -f "$nginx_config" "$nginx_enabled"
  fi
  nginx -t >/dev/null && systemctl reload nginx || true
}

if [ -f "$nginx_config" ]; then
  nginx_backup="$(mktemp)"
  cp -a "$nginx_config" "$nginx_backup"
fi
echo "$nginx_config_b64" | base64 -d > "$nginx_config"
ln -sfn "../sites-available/$nginx_site_name.conf" "$nginx_enabled"

rollback() {
  if [ -n "$previous_release" ] && [ -d "$previous_release" ]; then
    ln -sfn "$previous_release" "$release_root/current"
    nginx -t >/dev/null && systemctl reload nginx || true
  fi
}

if ! nginx -t; then restore_nginx_config; rollback; exit 1; fi
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
  '__NGINX_SITE_NAME__' = $nginxSiteName; '__NGINX_CONFIG_B64__' = $encodedNginxConfig
}
foreach ($token in $values.Keys) { $serverScript = $serverScript.Replace($token, $values[$token]) }
$encodedServerScript = [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($serverScript))
& ssh @sshBase $remote "echo $encodedServerScript | base64 -d | bash"
if ($LASTEXITCODE -ne 0) { throw 'Publishing or website deployment failed. The prior release was restored if the health check failed.' }
Write-Host "Complete: $commit was pushed and deployed to $healthUrl"
