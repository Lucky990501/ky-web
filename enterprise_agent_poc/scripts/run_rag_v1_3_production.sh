#!/usr/bin/env sh
# Production-only entry point. The Python evaluator emits redacted JSON only.
set -eu

ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
PYTHON_BIN=${ENTERPRISE_POC_PYTHON_BIN:-python}

cd "$ROOT_DIR"
export PYTHONPATH="$ROOT_DIR${PYTHONPATH:+:$PYTHONPATH}"
if [ ! -r .env.production ]; then
  printf '%s\n' '{"status":"failed","error_type":"preflight_failed","message":"production_environment_file_missing"}'
  exit 2
fi

set -a
. ./.env.production
set +a

TENANT_ID=${1:-zhiy-e-intelligence}
if [ "$#" -gt 0 ]; then
  shift
fi
exec "$PYTHON_BIN" scripts/run_rag_v1_3_eval.py "$TENANT_ID" "$@"
