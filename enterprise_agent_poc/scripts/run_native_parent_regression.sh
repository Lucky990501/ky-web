#!/bin/sh
# Component regressions only. Full native proof has a separate systemd/PG run.
set -eu
repository=$1
application=$2
tooling=$3
inputs=$4
testpython=$5
taskroot=$(mktemp -d /tmp/native-parent-regression.XXXXXX)
git -c core.autocrlf=false clone --shared --no-checkout "$repository" "$taskroot/tooling"
git -C "$taskroot/tooling" -c core.autocrlf=false checkout --detach 1e11e1a4835a465963ec5bd88a5ed40d4b300086
mkdir "$taskroot/app"
git --git-dir="$repository" archive d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7 | tar -x -C "$taskroot/app"
for name in main.py product_service.py worker.py test_exact_admin_gate.py test_runtime_tooling.py test_tenant_seeding.py agent_runtime_test.py product_store.py agent_catalog_api.py; do
    cp "$application/enterprise_agent_poc/app/$name" "$taskroot/app/enterprise_agent_poc/app/$name"
done
for name in exact_test_admin_lifecycle.py wechat_runtime_native_successor.py run_exact_test_admin_lifecycle.py verify_runtime_release_integration.py native_parent_contract.py prepare_exact_admin_authority.py runtime_recovery_operator.py runtime_recovery_binding.py seeding_approval_selector.py seeding_release_binding.py verify_native_parent_contract.py verify_runtime_recovery_binding.py; do
    cp "$tooling/enterprise_agent_poc/scripts/$name" "$taskroot/tooling/enterprise_agent_poc/scripts/$name"
done
# Inherited 1e11 tests use its historical application adapters. Only the new
# neutral authority-root resolver is needed by the versioned recovery loader.
cp "$application/enterprise_agent_poc/app/test_runtime_tooling.py" "$taskroot/tooling/enterprise_agent_poc/app/test_runtime_tooling.py"
export PYTHONDONTWRITEBYTECODE=1
cp "$application/enterprise_agent_poc/tests/test_runtime_test_atomicity.py" "$taskroot/app/enterprise_agent_poc/tests/test_runtime_test_atomicity.py"
"$testpython" -B "$tooling/enterprise_agent_poc/scripts/verify_runtime_atomicity_components.py" --project "$taskroot/app/enterprise_agent_poc" --pg-bin /home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin
scripts="$taskroot/tooling/enterprise_agent_poc/scripts"
"$testpython" -B "$scripts/verify_runtime_recovery_binding.py"
"$testpython" -B "$scripts/verify_runtime_release_integration.py" --application-project "$taskroot/app/enterprise_agent_poc"
"$testpython" -B "$scripts/verify_native_parent_contract.py"
"$testpython" -B "$scripts/verify_exact_test_admin_lifecycle.py" --current-dca-source "$inputs/.wechat-dca-successor-v1/common.py"
"$testpython" -B "$scripts/verify_wechat_historical_prepare.py" --fixture "$inputs/WECHAT_PREPARE_AUTH_READONLY_FIXTURE_V1.json"
"$testpython" -B "$scripts/verify_wechat_historical_actions.py" --audit "$inputs/WECHAT_HISTORICAL_ACTION_CLASSIFICATION_V1_AUDIT.json" --prepare-fixture "$inputs/WECHAT_PREPARE_AUTH_READONLY_FIXTURE_V1.json"
"$testpython" -B "$scripts/verify_wechat_runtime_test_lifecycle_guard.py" --historical-dca-guard "$inputs/.wechat-dca-successor-v1/common.py" --snapshot "$inputs/WECHAT_RUNTIME_TEST_GUARD_READONLY_SNAPSHOT_V1.md" --current-native-root "$inputs/.wechat-persistent-config-guard-primary-v1"
"$testpython" -B "$scripts/verify_wechat_persistent_config_guard.py" --old-native-common "$inputs/.wechat-d8a-common-cache-v1/common.py"
"$testpython" -B "$scripts/verify_wechat_secret_provisioning.py"
"$testpython" -B "$scripts/verify_wechat_personal_config.py"
"$testpython" -B "$scripts/verify_tenant_agent_seeding.py" --native-guard-source "$inputs/.wechat-personal-core-resume-v1/common.py"
git -c core.autocrlf=false clone --shared --no-checkout "$repository" "$taskroot/provision-v2"
git -C "$taskroot/provision-v2" -c core.autocrlf=false checkout --detach 85a14513b6c863f8d506b86b3083a19d97b72799
"$testpython" -B "$scripts/verify_tenant_seeding_v2.py" --tooling-root "$taskroot/provision-v2" --native-guard-source "$inputs/.wechat-canonical-v2/common.py"
