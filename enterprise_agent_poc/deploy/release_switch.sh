#!/usr/bin/env bash
# Controlled systemd release switch. Run only as root on the production host.
set -euo pipefail

release_id="${1:?usage: release_switch.sh <trusted-release-id> [--preflight-only | --rollback-preflight <target-release-id> <target-source-commit>]}"
mode="${2:-}"
[[ ( $# -le 2 && ( -z "$mode" || "$mode" == "--preflight-only" ) ) || ( $# -eq 4 && "$mode" == "--rollback-preflight" ) ]] || { echo "invalid release mode" >&2; exit 2; }
base=/opt/enterprise-agent-workbench
release_root="$base/releases/$release_id/enterprise_agent_poc"
shared_env="$base/shared/enterprise-agent.env"
runtime_venv="$base/venv"
runtime_data_dir="$base/shared/runtime-data"
services=(enterprise-agent-api enterprise-agent-mcp enterprise-agent-worker)

case "$release_id" in
  ""|*[!A-Za-z0-9._-]*) echo "invalid release id" >&2; exit 2 ;;
esac
[[ -d "$release_root" && -f "$release_root/pyproject.toml" ]] || { echo "release source missing" >&2; exit 2; }
[[ -f "$shared_env" ]] || { echo "shared environment file missing" >&2; exit 2; }
[[ "$(stat -c %a "$shared_env")" =~ ^[0-6]00$ ]] || { echo "shared environment file must not be group/world readable" >&2; exit 2; }
[[ -x "$runtime_venv/bin/python" && -x "$runtime_venv/bin/uvicorn" ]] || { echo "managed runtime venv missing" >&2; exit 2; }
[[ -d "$runtime_data_dir" ]] || { echo "shared runtime data directory missing" >&2; exit 2; }

"$runtime_venv/bin/python" -c 'import openai_codex'
"$runtime_venv/bin/pip" check
set -a; . "$shared_env"; set +a
[[ -z "${ENTERPRISE_POC_DATA_DIR:-}" || "$ENTERPRISE_POC_DATA_DIR" == "$runtime_data_dir" ]] || { echo "shared DATA_DIR conflicts with controlled service configuration" >&2; exit 2; }
# The same existing deployment parameter feeds preflight and every service
# drop-in below. Never infer the production Registry root from a Release/cwd.
export ENTERPRISE_POC_DATA_DIR="$runtime_data_dir"
export PYTHONDONTWRITEBYTECODE=1
printf '{"data_dir_resolved":true}\n'
cd "$release_root"
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/verify_bundled_skills.py --data-dir "$runtime_data_dir"
# migrate.status() creates history even on an installed database. For BOTH
# modes use its unchanged checksum/record functions over a read-only SELECT.
migration_result=$(PYTHONPATH="$release_root" "$runtime_venv/bin/python" - <<'PY'
import json
from psycopg import connect
from psycopg.rows import dict_row
from scripts import migrate
try:
    with connect(migrate.settings.database_url, row_factory=dict_row,
                 options="-c default_transaction_read_only=on") as conn:
        if conn.execute("SHOW transaction_read_only").fetchone()["transaction_read_only"] != "on":
            raise RuntimeError("Read-only migration preflight required")
        rows = conn.execute("SELECT version,name,checksum,applied_at FROM schema_migrations ORDER BY version").fetchall()
    files = migrate.migration_items()
    applied = {row["version"]: dict(row) for row in rows}
    records = migrate.migration_records(files, applied)
    unknown = sorted(set(applied) - {item["version"] for item in files})
    mismatch = sum(item["compatibility_status"] == migrate.CHECKSUM_MISMATCH for item in records)
    pending = sum(item["compatibility_status"] == migrate.PENDING for item in records)
    print(json.dumps({"migrations": records, "unknown_history_versions": unknown,
                      "checksum_mismatch": mismatch, "pending": pending}, default=str))
    raise SystemExit(2 if unknown or mismatch else 0)
except Exception:
    print(json.dumps({"status": "BLOCKED", "check": "migration_read_only_preflight"}))
    raise SystemExit(2)
PY
)
printf '%s\n' "$migration_result"
config_result=$(PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/verify_runtime_config.py --environment-file "$shared_env")
printf '%s\n' "$config_result"
CONFIG_RESULT="$config_result" "$runtime_venv/bin/python" -c 'import json, os; data = json.loads(os.environ["CONFIG_RESULT"]); raise SystemExit(0 if data.get("matches") is True else 2)'
if [[ "$mode" == "--rollback-preflight" ]]; then
  PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/rollback_preflight.py --target-release-id "$3" --target-source-commit "$4"
  exit 0
fi
if [[ "$mode" == "--preflight-only" ]]; then
  printf '{"status":"preflight_passed","release_id":"%s","data_dir_resolved":true}\n' "$release_id"
  exit 0
fi

# The trusted gate reads the persistent Compatibility Epoch BEFORE target
# validation. A productized_v1 floor can never fall back to the old legacy
# target, even with no current V2 rows. This entry point NEVER advances Epoch.
# Prove that the previous application is approved for the COMPLETE planned
# schema before committing migrations or touching any service configuration.
current_link="$base/release-current"
previous=$(readlink -f "$current_link" 2>/dev/null || true)
[[ -n "$previous" && -d "$previous" ]] || { echo "approved rollback target missing" >&2; exit 2; }
rollback_target_id=$(basename "$(dirname "$previous")")
rollback_target_commit=$("$runtime_venv/bin/python" -c '
import json, re, sys
try:
    value=json.load(open(sys.argv[1]))["source_commit"]
    if not re.fullmatch("[0-9a-f]{40}", value): raise ValueError()
    print(value)
except Exception:
    raise SystemExit(2)
' "$base/releases/$rollback_target_id/$rollback_target_id.manifest.json")
[[ "$previous" == "$base/releases/$rollback_target_id/enterprise_agent_poc" ]] || { echo "rollback target outside controlled release path" >&2; exit 2; }
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/rollback_preflight.py --target-release-id "$rollback_target_id" --target-source-commit "$rollback_target_commit" --check-plan
if MIGRATION_RESULT="$migration_result" "$runtime_venv/bin/python" -c 'import json, os; raise SystemExit(0 if json.loads(os.environ["MIGRATION_RESULT"])["pending"] > 0 else 1)'; then
  PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/migrate.py up
fi

backup=$(mktemp -d "$base/.release-switch.XXXXXX")
for service in "${services[@]}"; do
  dropin="/etc/systemd/system/$service.service.d/release.conf"
  if [[ -f "$dropin" ]]; then
    cp "$dropin" "$backup/$service.conf"
  fi
done
rollback() {
  trap - ERR
  set +e
  # Never invoke the old runner: its future-history rejection is intentional.
  # Failure preserves backup/new state for manual intervention; no restore,
  # drop-in mutation, or restart is allowed until this trusted gate passes.
  if ! PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/rollback_preflight.py --target-release-id "$rollback_target_id" --target-source-commit "$rollback_target_commit"; then
    printf '{"status":"rollback_BLOCKED","services_restored":false}\n' >&2
    return 1
  fi
  ln -sfn "$previous" "$current_link" || return 1
  for service in "${services[@]}"; do
    dropin="/etc/systemd/system/$service.service.d/release.conf"
    if [[ -f "$backup/$service.conf" ]]; then install -D -m 0644 "$backup/$service.conf" "$dropin" || return 1; else rm -f "$dropin" || return 1; fi
  done
  systemctl daemon-reload || return 1
  systemctl restart "${services[@]/%/.service}" || return 1
  for service in "${services[@]}"; do systemctl is-active --quiet "$service.service" || return 1; done
  for attempt in $(seq 1 30); do
    if curl --fail --silent --show-error http://127.0.0.1:18090/api/health \
      | "$runtime_venv/bin/python" -c 'import json, sys; data=json.load(sys.stdin); raise SystemExit(0 if data.get("status") == "ok" and data.get("knowledge") == "ok" and data.get("environment") == "production" else 1)'; then
      printf '{"status":"rolled_back","application_release":"%s","schema_rollback":false}\n' "$rollback_target_id"
      rm -rf "$backup"
      return 0
    fi
    sleep 1
  done
  printf '{"status":"rollback_health_BLOCKED","schema_rollback":false}\n' >&2
  # Keep backup on failed rollback health too; never claim recovery succeeded.
  return 1
}
trap 'rollback; exit 1' ERR

for service in "${services[@]}"; do
  dropin="/etc/systemd/system/$service.service.d/release.conf"
  mkdir -p "$(dirname "$dropin")"
  cat > "$dropin" <<EOF
[Service]
WorkingDirectory=$release_root
EnvironmentFile=
EnvironmentFile=$shared_env
Environment=PYTHONPATH=$release_root
Environment=ENTERPRISE_POC_DATA_DIR=$runtime_data_dir
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
  if curl --fail --silent --show-error http://127.0.0.1:18090/api/health \
    | "$runtime_venv/bin/python" -c 'import json, sys; data = json.load(sys.stdin); raise SystemExit(0 if data.get("status") == "ok" and data.get("knowledge") == "ok" and data.get("environment") == "production" else 1)'; then
    break
  fi
  if [[ "$attempt" == 30 ]]; then
    echo "API health did not become ready within 30 seconds" >&2
    rollback
    exit 1
  fi
  sleep 1
done
trap - ERR
rm -rf "$backup"
printf '{"status":"switched","release_id":"%s","previous_release":"%s"}\n' "$release_id" "${previous:-none}"
