#!/usr/bin/env bash
# Controlled systemd release switch. Run only as root on the production host.
set -euo pipefail

release_id="${1:?usage: release_switch.sh <release-id>}"
base=/opt/enterprise-agent-workbench
release_root="$base/releases/$release_id/enterprise_agent_poc"
shared_env="$base/shared/enterprise-agent.env"
runtime_venv="$base/venv"
services=(enterprise-agent-api enterprise-agent-mcp enterprise-agent-worker)

case "$release_id" in
  ""|*[!A-Za-z0-9._-]*) echo "invalid release id" >&2; exit 2 ;;
esac
[[ -d "$release_root" && -f "$release_root/pyproject.toml" ]] || { echo "release source missing" >&2; exit 2; }
[[ -f "$shared_env" ]] || { echo "shared environment file missing" >&2; exit 2; }
[[ "$(stat -c %a "$shared_env")" =~ ^[0-6]00$ ]] || { echo "shared environment file must not be group/world readable" >&2; exit 2; }
[[ -x "$runtime_venv/bin/python" && -x "$runtime_venv/bin/uvicorn" ]] || { echo "managed runtime venv missing" >&2; exit 2; }

current_link="$base/release-current"
previous=$(readlink -f "$current_link" 2>/dev/null || true)
backup=$(mktemp -d "$base/.release-switch.XXXXXX")
rollback() {
  if [[ -n "$previous" && -d "$previous" ]]; then ln -sfn "$previous" "$current_link"; else rm -f "$current_link"; fi
  for service in "${services[@]}"; do
    dropin="/etc/systemd/system/$service.service.d/release.conf"
    if [[ -f "$backup/$service.conf" ]]; then install -D -m 0644 "$backup/$service.conf" "$dropin"; else rm -f "$dropin"; fi
  done
  systemctl daemon-reload
  systemctl restart "${services[@]/%/.service}" || true
}
trap 'rollback; exit 1' ERR

"$runtime_venv/bin/python" -c 'import openai_codex'
"$runtime_venv/bin/pip" check
set -a; . "$shared_env"; set +a
cd "$release_root"
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/migrate.py up
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/verify_runtime_config.py --environment-file "$shared_env" | grep -q '"matches": true'

for service in "${services[@]}"; do
  dropin="/etc/systemd/system/$service.service.d/release.conf"
  [[ -f "$dropin" ]] && cp "$dropin" "$backup/$service.conf"
  mkdir -p "$(dirname "$dropin")"
  cat > "$dropin" <<EOF
[Service]
WorkingDirectory=$release_root
EnvironmentFile=
EnvironmentFile=$shared_env
Environment=PYTHONPATH=$release_root
ExecStart=
EOF
  if [[ "$service" == enterprise-agent-api ]]; then
    echo "ExecStart=$runtime_venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 18090" >> "$dropin"
  elif [[ "$service" == enterprise-agent-mcp ]]; then
    echo "ExecStart=$runtime_venv/bin/python -m app.platform_mcp.server" >> "$dropin"
  else
    echo "ExecStart=$runtime_venv/bin/python -m app.worker" >> "$dropin"
  fi
done
ln -sfn "$release_root" "$current_link"
systemctl daemon-reload
systemctl restart enterprise-agent-mcp.service enterprise-agent-api.service enterprise-agent-worker.service
for service in "${services[@]}"; do systemctl is-active --quiet "$service.service"; done
for attempt in $(seq 1 30); do
  if curl --fail --silent --show-error http://127.0.0.1:18090/api/health >/dev/null; then
    break
  fi
  if [[ "$attempt" == 30 ]]; then
    echo "API health did not become ready within 30 seconds" >&2
    exit 1
  fi
  sleep 1
done
trap - ERR
rm -rf "$backup"
printf '{"status":"switched","release_id":"%s","previous_release":"%s"}\n' "$release_id" "${previous:-none}"
