#!/usr/bin/env bash
# Selected SQLite/scripted-runtime checks only. Not Full pytest / Stage 2 /
# Candidate Build / PG-Redis-Worker validation. No services or real Providers.
set -euo pipefail
repo=${B1_SOURCE_ROOT:-$(git rev-parse --show-toplevel)}
git_cmd=(git -c core.filemode=false -c core.safecrlf=false)
if [[ -n "${B1_GIT_DIR:-}" ]]; then git_cmd+=(--git-dir="$B1_GIT_DIR" --work-tree="$repo"); fi
python_bin=${B1_FIXTURE_PYTHON:?Set an existing test Python interpreter}
snapshot=$(mktemp -d /tmp/v6-b1-fixture.XXXXXXXX)
"${git_cmd[@]}" archive HEAD enterprise_agent_poc | tar -x -C "$snapshot"
if ! "${git_cmd[@]}" diff --quiet HEAD -- enterprise_agent_poc; then
  "${git_cmd[@]}" diff --binary HEAD -- enterprise_agent_poc | (cd "$snapshot" && git apply --unsafe-paths -)
fi
for relative in enterprise_agent_poc/app/copywriting_context.py enterprise_agent_poc/tests/test_dual_portal_b1.py; do
  cp "$repo/$relative" "$snapshot/$relative"
  chmod 644 "$snapshot/$relative"
done
printf 'FIXTURE_SNAPSHOT=%s\nBASE_SOURCE=%s\n' "$snapshot" "$("${git_cmd[@]}" rev-parse HEAD)"
"${git_cmd[@]}" diff --binary HEAD -- enterprise_agent_poc | sha256sum
cd "$snapshot/enterprise_agent_poc"
"$python_bin" -m pytest tests/test_dual_portal_b1.py tests/test_product_auth.py tests/test_task_history_ownership.py tests/test_workbench_routes.py -q --tb=short 2>&1 | tee "$snapshot/fixture-results.txt"
