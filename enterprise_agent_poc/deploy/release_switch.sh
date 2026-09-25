#!/usr/bin/env bash
# Controlled systemd release switch. Run only as root on the production host.
set -euo pipefail
final_status=PRODUCTION_DEPLOYMENT_PREFLIGHT_BLOCKED
report_exit() {
  result=$?
  if [[ "$result" -ne 0 ]]; then printf '{"deployment_status":"%s"}\n' "$final_status" >&2; fi
}
trap report_exit EXIT

release_id="${1:?usage: release_switch.sh <trusted-release-id> [--preflight-only | --rollback-preflight <target-release-id> <target-source-commit>]}"
mode="${2:-}"
[[ ( $# -le 2 && ( -z "$mode" || "$mode" == "--preflight-only" ) ) || ( $# -eq 4 && "$mode" == "--rollback-preflight" ) ]] || { echo "invalid release mode" >&2; exit 2; }
base=/opt/enterprise-agent-workbench
release_root="$base/releases/$release_id/enterprise_agent_poc"
shared_env="$base/shared/enterprise-agent.env"
runtime_venv="$base/venv"
runtime_data_dir="$base/shared/runtime-data"
services=(enterprise-agent-api enterprise-agent-mcp enterprise-agent-worker)
# The production API lifespan includes a bounded 45-second embedding probe.
# Keep a finite allowance above that probe for process startup; never weaken
# the required healthy-production JSON or the exact-predecessor rollback gate.
api_readiness_attempts=60

case "$release_id" in
  ""|*[!A-Za-z0-9._-]*) echo "invalid release id" >&2; exit 2 ;;
esac
# Serialize every release decision and side effect, including preflight and
# rollback.  The descriptor remains open until this shell exits, so the kernel
# releases it even if the process is interrupted.
release_lock_file="${RELEASE_LOCK_FILE:-/run/lock/enterprise-agent-workbench-release.lock}"
if ! command -v flock >/dev/null 2>&1; then
  printf '{"status":"BLOCKED","check":"RELEASE_SWITCH_LOCK_UNAVAILABLE"}\n' >&2
  exit 2
fi
if ! exec 9>"$release_lock_file"; then
  printf '{"status":"BLOCKED","check":"RELEASE_SWITCH_LOCK_UNAVAILABLE"}\n' >&2
  exit 2
fi
lock_status=0
flock -n -E 75 9 || lock_status=$?
if [[ "$lock_status" -eq 75 ]]; then
  printf '{"status":"BLOCKED","check":"RELEASE_SWITCH_LOCKED"}\n' >&2
  exit 75
fi
if [[ "$lock_status" -ne 0 ]]; then
  printf '{"status":"BLOCKED","check":"RELEASE_SWITCH_LOCK_UNAVAILABLE"}\n' >&2
  exit 2
fi
[[ -d "$release_root" && -f "$release_root/pyproject.toml" ]] || { echo "release source missing" >&2; exit 2; }
[[ -f "$shared_env" ]] || { echo "shared environment file missing" >&2; exit 2; }
[[ "$(stat -c %a "$shared_env")" =~ ^[0-6]00$ ]] || { echo "shared environment file must not be group/world readable" >&2; exit 2; }
[[ -x "$runtime_venv/bin/python" && -x "$runtime_venv/bin/uvicorn" ]] || { echo "managed runtime venv missing" >&2; exit 2; }
[[ -d "$runtime_data_dir" ]] || { echo "shared runtime data directory missing" >&2; exit 2; }

"$runtime_venv/bin/python" -c 'import openai_codex'
if ! "$runtime_venv/bin/python" -c 'from PIL import Image; import PIL'; then
  printf '{"status":"BLOCKED","check":"PILLOW_RUNTIME_MISSING"}\n' >&2
  exit 2
fi
"$runtime_venv/bin/pip" check
set -a; . "$shared_env"; set +a
[[ -z "${ENTERPRISE_POC_DATA_DIR:-}" || "$ENTERPRISE_POC_DATA_DIR" == "$runtime_data_dir" ]] || { echo "shared DATA_DIR conflicts with controlled service configuration" >&2; exit 2; }
# The same existing deployment parameter feeds preflight and every service
# drop-in below. Never infer the production Registry root from a Release/cwd.
export ENTERPRISE_POC_DATA_DIR="$runtime_data_dir"
export PYTHONDONTWRITEBYTECODE=1
current_link="$base/release-current"
previous=$(readlink -f "$current_link" 2>/dev/null || true)
[[ -n "$previous" && -d "$previous" ]] || { echo "approved rollback target missing" >&2; exit 2; }
rollback_target_id=$(basename "$(dirname "$previous")")
candidate_manifest="$base/releases/$release_id/$release_id.manifest.json"
predecessor_manifest="$base/releases/$rollback_target_id/$rollback_target_id.manifest.json"
[[ -f "$candidate_manifest" && -f "$predecessor_manifest" ]] || { echo "release manifest missing" >&2; exit 2; }
printf '{"data_dir_resolved":true}\n'
cd "$release_root"
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/verify_bundled_skills.py --data-dir "$runtime_data_dir" --candidate-release-manifest "$candidate_manifest" --predecessor-release-manifest "$predecessor_manifest"
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
declared_forward=$($runtime_venv/bin/python -c 'import json,sys; print("yes" if "forward_migrations" in json.load(open(sys.argv[1])) else "no")' "$candidate_manifest")
if [[ "$declared_forward" == yes ]]; then
  # The plan gate reads PostgreSQL in a read-only transaction. No DDL runs in
  # either preflight-only or rollback-preflight mode.
  PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_migration_transition.py preflight \
    --candidate-manifest "$candidate_manifest"
else
  MIGRATION_RESULT="$migration_result" "$runtime_venv/bin/python" -c 'import json, os; raise SystemExit(0 if json.loads(os.environ["MIGRATION_RESULT"])["pending"] == 0 else 2)'
fi
# The script owns FD 9 continuously: final preflight, snapshot, first mutation,
# smoke, final state and commit/recovery. No outer lock or lock handoff exists.
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_verify.py preflight \
  --candidate-manifest "$candidate_manifest" --predecessor-manifest "$predecessor_manifest"
if [[ "$mode" == "--preflight-only" ]]; then
  printf '{"status":"preflight_passed","release_id":"%s","data_dir_resolved":true}\n' "$release_id"
  exit 0
fi

# The trusted gate reads the persistent Compatibility Epoch BEFORE target
# validation. A productized_v1 floor can never fall back to the old legacy
# target, even with no current V2 rows. This entry point NEVER advances Epoch.
# Prove that the previous application is approved for the COMPLETE planned
# schema before committing migrations or touching any service configuration.
rollback_target_commit=$("$runtime_venv/bin/python" -c '
import json, re, sys
try:
    value=json.load(open(sys.argv[1]))["source_commit"]
    if not re.fullmatch("[0-9a-f]{40}", value): raise ValueError()
    print(value)
except Exception:
    raise SystemExit(2)
' "$predecessor_manifest")
[[ "$previous" == "$base/releases/$rollback_target_id/enterprise_agent_poc" ]] || { echo "rollback target outside controlled release path" >&2; exit 2; }
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/rollback_preflight.py --target-release-id "$rollback_target_id" --target-source-commit "$rollback_target_commit" --check-plan
backup=$(mktemp -d "$base/.release-switch.XXXXXX")
for service in "${services[@]}"; do
  dropin="/etc/systemd/system/$service.service.d/release.conf"
  if [[ -f "$dropin" ]]; then
    cp "$dropin" "$backup/$service.conf"
  fi
done
verify_release() {
  local step="$1"; shift
  printf '%s\n' "$step" > "$backup/last-step.log"
  local result=0
  PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_verify.py "$@" \
    --candidate-manifest "$candidate_manifest" --predecessor-manifest "$predecessor_manifest" \
    --snapshot "$backup/state.json" > "$backup/$step.log" 2>&1 || result=$?
  cat "$backup/$step.log"
  return "$result"
}
verify_release capture capture
rollback() {
  trap - ERR
  set +e
  # No code-only restore or unknown-state overwrite. This read-only guard also
  # protects unrelated agents and legacy releases. Keep the snapshot on failure.
  verify_release rollback-guard rollback-guard || return 1
  # Never invoke the old runner: its future-history rejection is intentional.
  # Failure preserves backup/new state for manual intervention; no restore,
  # drop-in mutation, or restart is allowed until this trusted gate passes.
  if ! PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/rollback_preflight.py --target-release-id "$rollback_target_id" --target-source-commit "$rollback_target_commit" > "$backup/rollback-identity.log" 2>&1; then
    cat "$backup/rollback-identity.log"
    printf '{"status":"rollback_BLOCKED","services_restored":false}\n' >&2
    return 1
  fi
  cat "$backup/rollback-identity.log"
  local transition_status=0
  PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_binding_transition.py --candidate-manifest "$candidate_manifest" --predecessor-manifest "$predecessor_manifest" --bundle-root "$release_root/skill_packages" --data-dir "$runtime_data_dir" --rollback > "$backup/rollback-helper.log" 2>&1 || transition_status=$?
  cat "$backup/rollback-helper.log"
  [[ "$transition_status" -eq 0 ]] || return 1
  ln -sfn "$previous" "$current_link" || return 1
  for service in "${services[@]}"; do
    dropin="/etc/systemd/system/$service.service.d/release.conf"
    if [[ -f "$backup/$service.conf" ]]; then install -D -m 0644 "$backup/$service.conf" "$dropin" || return 1; else rm -f "$dropin" || return 1; fi
  done
  systemctl daemon-reload || return 1
  systemctl restart "${services[@]/%/.service}" || return 1
  for service in "${services[@]}"; do systemctl is-active --quiet "$service.service" || return 1; done
  for attempt in $(seq 1 "$api_readiness_attempts"); do
    if curl --fail --silent --show-error http://127.0.0.1:18090/api/health \
      | tee "$backup/rollback-health.log" \
      | "$runtime_venv/bin/python" -c 'import json, sys; data=json.load(sys.stdin); raise SystemExit(0 if data.get("status") == "ok" and data.get("knowledge") == "ok" and data.get("environment") == "production" else 1)'; then
      verify_release restored state --rollback || return 1
      final_status=PRODUCTION_DEPLOYMENT_ROLLED_BACK
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
fail_release() {
  # Suppress recursion, not recovery evidence. FD 9 and snapshot stay alive.
  trap - ERR
  final_status=PRODUCTION_DEPLOYMENT_MANUAL_RECOVERY_REQUIRED
  cp "$backup/last-step.log" "$backup/failure-step.log" || true
  if ! rollback; then
    verify_release evidence evidence || printf '{"status":"recovery_evidence_incomplete","snapshot_retained":true}\n' >&2
  fi
  exit 1
}
trap 'fail_release' ERR
final_status=PRODUCTION_DEPLOYMENT_MANUAL_RECOVERY_REQUIRED

if [[ "$declared_forward" == yes ]]; then
  printf 'declared-forward-migrations\n' > "$backup/last-step.log"
  if ! PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_migration_transition.py apply \
      --candidate-manifest "$candidate_manifest" > "$backup/migration-apply.log" 2>&1; then
    cat "$backup/migration-apply.log"
    # DDL runs in one PostgreSQL transaction. If its outcome cannot be proven,
    # preserve the snapshot and evidence under the lock; never run a down SQL.
    trap - ERR
    verify_release evidence evidence || true
    exit 1
  fi
  cat "$backup/migration-apply.log"
  PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_migration_transition.py verify \
    --candidate-manifest "$candidate_manifest"
  printf 'predecessor-on-schema-014\n' > "$backup/last-step.log"
  verify_release predecessor-on-schema-014 state --rollback
fi

printf 'binding-apply\n' > "$backup/last-step.log"
PYTHONPATH="$release_root" "$runtime_venv/bin/python" scripts/release_binding_transition.py --candidate-manifest "$candidate_manifest" --predecessor-manifest "$predecessor_manifest" --bundle-root "$release_root/skill_packages" --data-dir "$runtime_data_dir" --apply

printf 'service-activation\n' > "$backup/last-step.log"
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
printf 'candidate-health\n' > "$backup/last-step.log"
for attempt in $(seq 1 "$api_readiness_attempts"); do
  if curl --fail --silent --show-error http://127.0.0.1:18090/api/health \
    | tee "$backup/candidate-health.log" \
    | "$runtime_venv/bin/python" -c 'import json, sys; data = json.load(sys.stdin); raise SystemExit(0 if data.get("status") == "ok" and data.get("knowledge") == "ok" and data.get("environment") == "production" else 1)'; then
    break
  fi
  if [[ "$attempt" == "$api_readiness_attempts" ]]; then
    echo "API health did not become ready within ${api_readiness_attempts} seconds" >&2
    fail_release # same rollback path, with evidence on failed recovery
  fi
  sleep 1
done
verify_release before-smoke state
verify_release technical-smoke smoke
verify_release final-state state
# RELEASE COMMIT POINT: same global lock + rollback snapshot through all gates.
trap - ERR
rm -rf "$backup"
final_status=PRODUCTION_DEPLOYMENT_PASS
printf '{"status":"switched","deployment_status":"%s","release_id":"%s","previous_release":"%s"}\n' "$final_status" "$release_id" "${previous:-none}"
