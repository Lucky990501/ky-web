#!/usr/bin/env bash
# Controlled systemd release switch. Run only as root on the production host.
set -euo pipefail

release_id="${1:?usage: release_switch.sh <release-id>}"
base=/opt/enterprise-agent-workbench
release_root="$base/releases/$release_id/enterprise_agent_poc"
shared_env="$base/shared/enterprise-agent.env"
services=(enterprise-agent-api enterprise-agent-mcp enterprise-agent-worker)

case "$release_id" in *[!A-Za-z0-9._-]*|'') echo "invalid release id" >&2; exit 2;; esac
[[ -d "$release_root" && -f "$release_root/pyproject.toml" ]] || { echo "release source missing" >&2; exit 2; }
[[ -f "$shared_env" ]] || { echo "shared environment file missing" >&2; exit 2; }
[[ "$(stat -c %a "$shared_env")" =~ ^[0-6]00$ ]] || { echo "shared environment file must not be group/world readable" >&2; exit 2; }

previous=$(readlink -f "$base/current" 2>/dev/null || true)
backup=$(mktemp -d "$base/.release-switch.XXXXXX")
rollback() {
  if [[ -n "$previous" && -d "$previous" ]]; then ln -sfn "$previous" "$base/current"; else rm -f "$base/current"; fi
  for service in "${services[@]}"; do
    dropin="/etc/systemd/system/$service.service.d/release.conf"
    if [[ -f "$backup/$service.conf" ]]; then install -D -m 0644 "$backup/$service.conf" "$dropin"; else rm -f "$dropin"; fi
  done
  systemctl daemon-reload
  systemctl restart "${services[@]/%/.service}" || true
}
trap 'rollback; exit 1' ERR

python3 -m venv "$release_root/.venv"
"$release_root/.venv/bin/pip" install --disable-pip-version-check --no-input "$release_root"
set -a; . "$shared_env"; set +a
"$release_root/.venv/bin/python" "$release_root/scripts/migrate.py" up
"$release_root/.venv/bin/python" "$release_root/scripts/verify_runtime_config.py" --environment-file "$shared_env" | grep -q '"matches": true'

for service in "${services[@]}"; do
  dropin="/etc/systemd/system/$service.service.d/release.conf"
  [[ -f "$dropin" ]] && cp "$dropin" "$backup/$service.conf"
  mkdir -p "$(dirname "$dropin")"
  cat > "$dropin" <<EOF
[Service]
WorkingDirectory=$release_root
EnvironmentFile=$shared_env
ExecStart=
EOF
  if [[ "$service" == enterprise-agent-api ]]; then
    echo "ExecStart=$release_root/.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18090" >> "$dropin"
  elif [[ "$service" == enterprise-agent-mcp ]]; then
    echo "ExecStart=$release_root/.venv/bin/python -m app.platform_mcp.server" >> "$dropin"
  else
    echo "ExecStart=$release_root/.venv/bin/python -m app.worker" >> "$dropin"
  fi
done
ln -sfn "$release_root" "$base/current"
systemctl daemon-reload
systemctl restart enterprise-agent-mcp.service enterprise-agent-api.service enterprise-agent-worker.service
for service in "${services[@]}"; do systemctl is-active --quiet "$service.service"; done
curl --fail --silent --show-error http://127.0.0.1:18090/api/health >/dev/null
trap - ERR
rm -rf "$backup"
printf '{"status":"switched","release_id":"%s","previous_release":"%s"}\n' "$release_id" "${previous:-none}"
