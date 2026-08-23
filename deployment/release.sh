#!/bin/zsh
# Deploy the static site from macOS. Run without --execute to validate locally.
set -euo pipefail

die() {
  print -u2 -- "$1"
  exit 1
}

extract_powershell_template() {
  local marker="$1"
  local source_file="$2"
  awk -v marker="$marker" '
    $0 == "$" marker " = @\047" { capture = 1; next }
    capture && $0 == "\047@" { exit }
    capture { print }
  ' "$source_file"
}

project_root=''
execute=0
while (( $# )); do
  case "$1" in
    --project-root)
      (( $# >= 2 )) || die 'Missing value for --project-root.'
      project_root="$2"
      shift 2
      ;;
    --execute)
      execute=1
      shift
      ;;
    *)
      die "Unknown argument: $1"
      ;;
  esac
done

[[ -n "$project_root" && -d "$project_root" ]] || die 'A valid project root is required.'
project_root="$(cd -- "$project_root" && pwd -P)"
secret_file="$project_root/.deploy-secrets.local"
powershell_release="$project_root/deployment/release.ps1"
[[ -f "$secret_file" ]] || die "Local deploy config is missing: $secret_file. Copy .deploy-secrets.example to .deploy-secrets.local and fill it locally."
[[ -f "$powershell_release" ]] || die "Windows release template is missing: $powershell_release"

typeset -A config
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  [[ -z "${line//[[:space:]]/}" || "$line" == \#* ]] && continue
  [[ "$line" == *=* ]] || continue
  key="${line%%=*}"
  value="${line#*=}"
  [[ "$key" =~ ^[A-Z][A-Z0-9_]*$ ]] || die "Invalid deployment setting name: $key"
  config[$key]="$value"
done < "$secret_file"

config_value() {
  local name="$1"
  local fallback="${2:-}"
  print -r -- "${config[$name]:-$fallback}"
}

project_name="$(config_value PROJECT_NAME "${project_root:t}")"
server_host="$(config_value SERVER_HOST)"
server_user="$(config_value SERVER_USER)"
server_port="$(config_value SERVER_PORT 22)"
stage_dir="$(config_value SERVER_STAGE_DIR "/root/${project_name}-bridge")"
key_path="$(config_value SERVER_SSH_KEY_PATH)"
github_remote="$(config_value GITHUB_REMOTE)"
branch="$(config_value GITHUB_BRANCH main)"
release_root="$(config_value SERVER_RELEASE_ROOT "$(config_value SERVER_SITE_ROOT "/var/www/${project_name}")")"
site_host="$(config_value HEALTHCHECK_HOST "$(config_value SERVER_SITE_HOST)")"
health_url="$(config_value HEALTHCHECK_URL "${site_host:+https://${site_host}/}")"
origin_health_url="$(config_value ORIGIN_HEALTHCHECK_URL)"
origin_health_host="$(config_value ORIGIN_HEALTHCHECK_HOST "$site_host")"
nginx_site_name="$(config_value NGINX_SITE_NAME "$project_name")"
nginx_server_name="$(config_value NGINX_SERVER_NAME "$site_host")"
nginx_certificate="$(config_value NGINX_TLS_CERTIFICATE)"
nginx_certificate_key="$(config_value NGINX_TLS_CERTIFICATE_KEY)"
nginx_ssl_options="$(config_value NGINX_SSL_OPTIONS /etc/letsencrypt/options-ssl-nginx.conf)"
nginx_dhparam="$(config_value NGINX_SSL_DHPARAM /etc/letsencrypt/ssl-dhparams.pem)"

typeset -A required
required=(
  SERVER_HOST "$server_host"
  SERVER_USER "$server_user"
  SERVER_PORT "$server_port"
  SERVER_SSH_KEY_PATH "$key_path"
  GITHUB_REMOTE "$github_remote"
  GITHUB_BRANCH "$branch"
  SERVER_RELEASE_ROOT "$release_root"
  HEALTHCHECK_URL "$health_url"
  NGINX_SITE_NAME "$nginx_site_name"
  NGINX_SERVER_NAME "$nginx_server_name"
  NGINX_TLS_CERTIFICATE "$nginx_certificate"
  NGINX_TLS_CERTIFICATE_KEY "$nginx_certificate_key"
)
missing=()
for name value in ${(kv)required}; do
  [[ -n "$value" ]] || missing+=("$name")
done
(( ${#missing} == 0 )) || die "Deploy config is missing: ${(j:, :)missing}"

commit="$(git -C "$project_root" rev-parse --verify HEAD)" || die 'Unable to resolve the current Git commit.'
for shell_value in "$project_name" "$branch" "$commit" "$stage_dir" "$github_remote" "$release_root" "$site_host" "$health_url" "$origin_health_url" "$origin_health_host"; do
  [[ "$shell_value" != *"'"* && "$shell_value" != *$'\r'* && "$shell_value" != *$'\n'* ]] || die 'Deployment settings cannot contain quotes or line breaks.'
done
[[ "$nginx_site_name" =~ ^[A-Za-z0-9_-]+$ ]] || die 'NGINX_SITE_NAME may contain only letters, numbers, hyphens, and underscores.'
[[ "$nginx_server_name" =~ ^[A-Za-z0-9.-]+(\ [A-Za-z0-9.-]+)*$ ]] || die 'NGINX_SERVER_NAME must contain valid domain names separated by spaces.'
for nginx_path in "$nginx_certificate" "$nginx_certificate_key" "$nginx_ssl_options" "$nginx_dhparam"; do
  [[ "$nginx_path" =~ ^/[A-Za-z0-9._/-]+$ ]] || die 'Nginx file paths must be absolute paths without special characters.'
done

bundle_dir="$project_root/.deploy-bridge"
bundle_name="${project_name}-${commit}.bundle"
bundle_path="$bundle_dir/$bundle_name"
remote="${server_user}@${server_host}"
print -- "Release: ${project_name}@${commit}"
print -- "Route: local Git bundle -> ${remote}:${stage_dir} -> GitHub -> ${health_url}"

if (( ! execute )); then
  print -- 'Simulation only: no upload, Git push, or server deployment was performed.'
  exit 0
fi

[[ "$key_path" != *'path\to'* && "$key_path" != *'<'* ]] || die 'Set SERVER_SSH_KEY_PATH in .deploy-secrets.local before executing.'
[[ -f "$key_path" ]] || die "SSH key file was not found: $key_path"
mkdir -p "$bundle_dir"
git -C "$project_root" bundle create "$bundle_path" "$branch" || die 'Unable to create the Git bundle.'
ssh -i "$key_path" -p "$server_port" "$remote" "mkdir -p '$stage_dir'" || die 'Unable to create the server bridge directory.'
scp -i "$key_path" -P "$server_port" "$bundle_path" "${remote}:${stage_dir}/" || die 'Unable to upload the Git bundle to the server.'

nginx_config="$(extract_powershell_template nginxConfig "$powershell_release")"
[[ -n "$nginx_config" ]] || die 'Unable to load the managed Nginx template.'
nginx_config="${nginx_config//__NGINX_SERVER_NAME__/$nginx_server_name}"
nginx_config="${nginx_config//__RELEASE_ROOT__/$release_root}"
nginx_config="${nginx_config//__NGINX_CERTIFICATE__/$nginx_certificate}"
nginx_config="${nginx_config//__NGINX_CERTIFICATE_KEY__/$nginx_certificate_key}"
nginx_config="${nginx_config//__NGINX_SSL_OPTIONS__/$nginx_ssl_options}"
nginx_config="${nginx_config//__NGINX_DHPARAM__/$nginx_dhparam}"
encoded_nginx_config="$(print -rn -- "$nginx_config" | base64 | tr -d '\n')"

server_script="$(extract_powershell_template serverScript "$powershell_release")"
[[ -n "$server_script" ]] || die 'Unable to load the server release template.'
server_script="${server_script//__STAGE_DIR__/$stage_dir}"
server_script="${server_script//__BUNDLE_NAME__/$bundle_name}"
server_script="${server_script//__BRANCH__/$branch}"
server_script="${server_script//__COMMIT__/$commit}"
server_script="${server_script//__GITHUB_REMOTE__/$github_remote}"
server_script="${server_script//__RELEASE_ROOT__/$release_root}"
server_script="${server_script//__HEALTH_URL__/$health_url}"
server_script="${server_script//__HEALTH_HOST__/$site_host}"
server_script="${server_script//__ORIGIN_HEALTH_URL__/$origin_health_url}"
server_script="${server_script//__ORIGIN_HEALTH_HOST__/$origin_health_host}"
server_script="${server_script//__PROJECT_NAME__/$project_name}"
server_script="${server_script//__NGINX_SITE_NAME__/$nginx_site_name}"
server_script="${server_script//__NGINX_CONFIG_B64__/$encoded_nginx_config}"
encoded_server_script="$(print -rn -- "$server_script" | base64 | tr -d '\n')"

ssh -i "$key_path" -p "$server_port" "$remote" "echo $encoded_server_script | base64 -d | bash" || die 'Publishing or website deployment failed. The prior release was restored if the health check failed.'
print -- "Complete: $commit was pushed and deployed to $health_url"
