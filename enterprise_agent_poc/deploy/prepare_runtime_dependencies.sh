#!/usr/bin/env bash
# Explicit candidate-staging step: install declared Pillow into the managed
# runtime before release_switch.sh preflight. Never runs during a dry-run gate.
set -euo pipefail

release_id="${1:?usage: prepare_runtime_dependencies.sh <trusted-release-id> --execute}"
[[ "${2:-}" == "--execute" && $# -eq 2 ]] || { echo "--execute is required" >&2; exit 2; }
case "$release_id" in
  ""|*[!A-Za-z0-9._-]*) echo "invalid release id" >&2; exit 2 ;;
esac

base=/opt/enterprise-agent-workbench
release_root="$base/releases/$release_id/enterprise_agent_poc"
runtime_venv="$base/venv"
[[ -f "$release_root/pyproject.toml" ]] || { echo "candidate pyproject missing" >&2; exit 2; }
[[ -x "$runtime_venv/bin/python" && -x "$runtime_venv/bin/pip" ]] || { echo "managed runtime missing" >&2; exit 2; }

requirement=$("$runtime_venv/bin/python" - "$release_root/pyproject.toml" <<'PY'
import pathlib
import sys
import tomllib

project = tomllib.loads(pathlib.Path(sys.argv[1]).read_text(encoding="utf-8"))
matches = [value for value in project["project"]["dependencies"] if value.lower().startswith("pillow")]
if len(matches) != 1 or matches[0] != "Pillow>=10,<13":
    raise SystemExit("candidate Pillow dependency contract mismatch")
print(matches[0])
PY
)

"$runtime_venv/bin/pip" install --disable-pip-version-check --no-input --only-binary=:all: "$requirement"
"$runtime_venv/bin/python" -c 'from PIL import Image; import PIL'
"$runtime_venv/bin/pip" check
printf '{"status":"READY","dependency":"%s"}\n' "$requirement"
