# WeChat Runtime Test Guard Candidate Freeze & Handoff V1

Date: 2026-10-09 (Asia/Shanghai). Recipient: 06 | Test / Release Environment Migration.

## Status and publication boundary

- `CODE_IMPLEMENTATION = READY`
- `OFFLINE_REGRESSION = PASS`
- `NATIVE_FULL_ENTRY = NOT_VERIFIED`
- `INSTALL_AUTHORITY = NOT_GRANTED`

This is an **unqualified source Candidate**, not a formally installable Guard. Controller authorized one normal commit/push/fresh remote verification, not PRIMARY installation, Grant, Runtime Test, Publish, Provider admission or deployment. The remaining qualification failure is `NATIVE_SUCCESSOR_FULL_ENTRY_ISOLATED_VERIFICATION_NOT_PROVEN`.

Branch: `codex/wechat-exact-test-admin-lifecycle-v1`.
Direct parent: `3d4f57a445fbdfa4b1761e29c56d06aea1f909a4` / tree `6dcf595975eebfc628e3a68e5c742270c71b14e0`.
Final `NATIVE_GUARD_CANDIDATE_SOURCE` and `NATIVE_GUARD_CANDIDATE_TREE` are the publication receipt, not self-referential placeholders in a seal. Use that exact commit/tree when constructing any future approved successor.

PRIMARY Application remains `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7` / tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`. No historical commit, Guard, Seal, Receipt, configuration or secret was rewritten. The Candidate includes the necessary Application Test request gate: the old global platform membership boolean cannot enforce exact route/Agent/Revision/run restrictions by Tooling alone. Application and Tooling successor identities must therefore both equal this new Candidate commit/tree; installation under d8a's existing seal is forbidden.

## Frozen file set

1. `enterprise_agent_poc/app/main.py`: three lines wiring the Test-only gate.
2. `enterprise_agent_poc/app/test_exact_admin_gate.py`: signed-session, exact scoped platform requests and existing Runtime/Worker isolation callbacks.
3. `enterprise_agent_poc/scripts/exact_test_admin_lifecycle.py`: transactional exact Grant/Revoke and audit service.
4. `enterprise_agent_poc/scripts/run_exact_test_admin_lifecycle.py`: formal native operator CLI.
5. `enterprise_agent_poc/scripts/verify_exact_test_admin_lifecycle.py`: offline verifier, **not** native qualification entry.
6. `enterprise_agent_poc/scripts/wechat_runtime_native_successor.py`: full native wrapper and lifecycle projection into mandatory predecessor checks.
7. `enterprise_agent_poc/scripts/wechat_runtime_test_lifecycle_guard.py`: exact adapter integration into inherited A/B/C checks.
8. `enterprise_agent_poc/tests/test_exact_test_admin_lifecycle.py`: 28 directed admin/native-component tests.
9. `enterprise_agent_poc/tests/test_wechat_runtime_test_lifecycle_guard.py`: isolated fixture opt-out from the old uncontrolled grant.
10. `docs/WECHAT_RUNTIME_TEST_ADMIN_LIFECYCLE_CLOSURE_V1_REPORT.md`: implementation, evidence and recovery limits.
11. This handoff report.

No Skill, Prompt, Secret backend, UI, Migration, schema or Production release code change. No new business capability was implemented in this freeze turn. Main workspace's seven tracked dirty files and historical untracked files were excluded. Review of Candidate source/diff plus secret-pattern filename scan found no real credential material; session/environment paths below are identities only, not secret values.

## Fresh offline regression

The same frozen code was re-executed in this turn with `E:/python3.13.0/python.exe -B` and `PYTHONDONTWRITEBYTECODE=1`.

| Verifier | PASS | failed / errors / raw skips |
| --- | ---: | --- |
| `verify_exact_test_admin_lifecycle.py` | 28 | 0 / 0 / 0 |
| `verify_wechat_runtime_test_lifecycle_guard.py` | 52 | 0 / 0 / 0 |
| `verify_wechat_persistent_config_guard.py` | 42 | 0 / 0 / 0 |
| `verify_wechat_secret_provisioning.py` | 60 | 0 / 0 / 0 |
| `verify_wechat_personal_config.py` | 23 | 0 / 0 / 0 |
| `verify_tenant_seeding_v2.py` | 95 | 0 / 0 / 0 |
| `verify_tenant_agent_seeding.py` | 30 | 0 / 0 / 0 |
| Total case executions | 330 | 0 / 0 / 0 |

These are targeted offline tests, not Full pytest, Stage 2, Candidate Build or PRIMARY execution. Positive PASS/FAIL records come from the formal signed-session API/executor with a Runtime double, not manually inserted positive PASS. SQLite/native-authority emulation and the pinned DCA structural function do not qualify the complete Linux native wrapper. The inherited 52-case verifier's `UNAVAILABLE_IN_D8A` describes historical d8a; the new scoped adapter is covered by the separate 28 cases. The Starlette/httpx deprecation warning is non-fatal; dependency changes were not mixed into the freeze.

## Actual native entry, UID and environment

Entry: `enterprise_agent_poc/scripts/run_exact_test_admin_lifecycle.py`.
It calls `scripts.wechat_runtime_native_successor.NativeSuccessor.verify()` for `status`; this is the complete source/authority/schema/snapshot/predecessor path, not a unit-test function.

The following are **future 06 isolated-Linux commands only**, after independent approval and fixture installation. None was executed here. Run from the exact successor checkout's `enterprise_agent_poc` directory using the approved Test venv Python, not a copied loose script. `CANDIDATE_CHECKOUT`, `TEST_PYTHON` and `EXACT_RUN_ID` must be selected from the approved isolated plan; no generic default run or live target is supplied.

```sh
cd "$CANDIDATE_CHECKOUT/enterprise_agent_poc"
export PYTHONDONTWRITEBYTECODE=1
export APP_ENV=test
export ENTERPRISE_POC_EXACT_TEST_ADMIN_REQUIRED=true
export ENTERPRISE_POC_BOOTSTRAP_DEMO_DATA=false
export ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED=true
export ENTERPRISE_POC_MODEL_PROVIDER_ID=disabled-test-provider
export ENTERPRISE_POC_MODEL_BASE_URL=http://127.0.0.1:9/disabled
# Load independently approved Test-only environment via its protected service/
# operator environment, never a Production credential or printed DSN/session.
"$TEST_PYTHON" -B scripts/run_exact_test_admin_lifecycle.py status --run-id "$EXACT_RUN_ID"
```

Expected execution user: existing Linux `lucky`, UID 1000, using established Test DB/Redis permissions. Root UID/GID 0 is for separately authorized protected approvals/attestations, not an excuse to run arbitrary writes. CLI does not enforce its own effective UID; 06 must attest UID and service parity. Session-path checks allow owner UID 0 or 1000. Native authority files and their ancestors enforce UID/GID 0, no symlinks/group/world write, and read-only files.

Required existing environment inputs:

- `ENTERPRISE_POC_DATABASE_URL`: exact PostgreSQL host `127.0.0.1`, port `55432`, database/role `enterprise_agent_test`; scheme postgres/postgresql, no query/fragment. Actual SQL identity checked with `host(inet_server_addr())`, port, current_database and current_user.
- `REDIS_URL`: predecessor requires `127.0.0.1:56379`, user `enterprise_agent_test`.
- Flags/model-disabled values as above; both `-B` and env=1 are mandatory.
- Existing remaining Test runtime settings, secret-reference metadata paths and service dependencies: preserve approved identities; do not copy credentials into this report or Git.

These addresses are **hard-coded existing contract requirements**, not authority to connect to PRIMARY. For full-entry isolation, 06 must arrange a separately approved namespace/isolated host/fixture that reproduces these exact addresses and fixed paths without touching the live DB, Redis, services or website. Do not point this CLI at live PRIMARY just to satisfy the constants; do not repurpose live ports or alter contracts ad hoc.

The inherited `base.check_env()` requires the disabled-model baseline. A later real Provider allowance would need separate compatible contract/network/budget authorization; this Candidate does not grant it.

## Exact Source, Scope and approval contracts

`PROJECT` is derived from the executing script's checkout. `git_identity(PROJECT)` and clean tracked Git status must match Scope. Application and Tooling Source/tree tuples must both equal the new Candidate identity. Installed source uses an exact complete path/SHA256 map under the repository root (only `.git` components excluded); extra/missing files, symlinks and bytecode fail. Compute approved byte pins from the exact Linux checkout and its declared installation map; do not substitute Windows CRLF working-copy bytes or conceal untracked files. No PYC blanket exclusion is present.

Fixed authority root: `/etc/enterprise-agent-test-exact-admin-v1`. Independent review must issue fresh files; this report does not issue approval, generate root seals or authorize their installation.

`scope.v1.json` exact keys:

```text
contract environment base_source base_tree application_source application_tree
tooling_source tooling_tree tenant_id principal_id principal_sha256 original_role
agent_id agent_slug revision_id fingerprint run_id operations issued_at expires_at
production_authority publication_authorized
```

Contract `EXACT_TEST_ADMIN_GRANT_REVOKE_V1`; environment test; immutable base d8a/tree above; new exact successor tuple; exact existing enabled member Principal/V2 canonical user hash; stable slug `wechat-official-account-writing`; exact UUID Principal/Agent/Revision/Run; exact Tenant; maximum one-hour aware-UTC lease. `operations` must equal the sorted fixed set `catalog_read, configure, enable, publish, runtime_test`. `production_authority=false`; `publication_authorized` is an independent boolean, not implied by granting execution.

`approval.v1.json` exact keys: `contract, scope_sha256, code_sha256, independent_approval`. `scope_sha256` is raw scope-file byte SHA. Independent approval object must exactly specify authority `PRIMARY_TEST_RELEASE_CONTROL`, authorization `TEST_ONLY_EXACT_ADMIN_LIFECYCLE_FIX_APPROVED`, production_authority false. Code map has exactly:

```text
scripts/exact_test_admin_lifecycle.py
scripts/run_exact_test_admin_lifecycle.py
app/test_exact_admin_gate.py
app/main.py
```

`native-approval.v1.json` exact keys: `contract, policy_sha256, code_sha256, independent_approval`. Contract `WECHAT_RUNTIME_TEST_NATIVE_SUCCESSOR_V1`; raw policy-file SHA; independent string `TEST_ONLY_EXACT_ADMIN_LIFECYCLE_FIX_APPROVED`. Code map has exactly:

```text
scripts/wechat_runtime_native_successor.py
scripts/wechat_runtime_test_lifecycle_guard.py
scripts/receipt_row_canonicalization.py
```

`native-policy.v1.json` exact keys:

```text
contract application_source application_tree files scope_sha256 schema_fingerprint
table_set runtime_scope anchors parent_pins ordinary_principals parent_files import_files
```

Here `scope_sha256` is the canonical semantic `wechat_runtime_test_lifecycle_guard.digest(scope)`, not the raw-file hash above. `files` is the full installed-source map; Schema fingerprint/table set must equal actual read-only snapshot and the predecessor Schema015 contract. `parent_pins` must retain existing historical protected row hashes. `anchors` must hash to those original pins for exactly `agent_templates`, `agent_template_versions`, `tenant_agent_instances`—never freshly repin published/current state. Ordinary principals are separately registered, not wildcard users.

`runtime_scope` follows the inherited `WECHAT_FORMAL_RUNTIME_TEST_LIFECYCLE_GUARD_V1` exact field contract:

```text
contract environment source tree tenant_id agent_id agent_slug revision_id fingerprint
actor_id actor_sha256 maximum_runtime_tests publication_allowed skill_id skill_revision_id
skill_sha256 model_config_id
```

The inherited inner Runtime identity intentionally remains its d8a Source/tree contract; the outer native Scope binds the actual new executing Source/tree independently. Actor/Agent/Revision/Tenant must join exactly between the two. Maximum runtime tests 1..8; model_config_id `codex-deepseek-v4-pro-high`; exact existing Skill revision/package checksum and configuration fingerprint. 06 must exercise this two-layer binding in full-entry isolation, not change inner constants or pretend the new Source is d8a.

Mandatory unchanged parent root `/etc/enterprise-agent-test-wechat-persistent-config-v1` loads exactly these five policy-pinned files:

```text
primary_guard.py                 SHA256 5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47
policy.v1.json
approval.v1.json
current-state-witness.v1.json
terminal-task-witness.v1.json
```

Import map must exactly pin:

```text
/etc/enterprise-agent-test-successor-wechat-d8a-seeding-approval-v1/common.py
  a40e35dc184746e7ba2f31a32e49054169c113645175aa98a455b26cdca72b09
/etc/enterprise-agent-test-successor-wechat-dca-v1/common.py
  0e079b0fd87db36be8efadff63ce129e90e1a5e20dfa27cf4228a83fd1e90d6b
```

The parent transitively loads its own protected Tooling, policies, Provision Receipt/Scope, current encrypted-record metadata and terminal witness. Do not replace it with a no-op/old Terminal-only validator. Actual path/owner/mode/schema/metadata compatibility remains a 06 full-entry acceptance item. Do not decrypt or reveal the existing AppSecret. New seeding/successor registration must bind the new Source without editing historical V1/V2 scope or reusing revoked authorization.

## Formal Admin and Runtime call chain

After approved isolation, operator CLI actions are:

```sh
# ALL FOUR BELOW MUTATE fixture DB/audits; forbidden in this turn and on live
# PRIMARY without a distinct reviewed installation/execution authority.
"$TEST_PYTHON" -B scripts/run_exact_test_admin_lifecycle.py prepare --run-id "$EXACT_RUN_ID"
"$TEST_PYTHON" -B scripts/run_exact_test_admin_lifecycle.py grant --run-id "$EXACT_RUN_ID"
"$TEST_PYTHON" -B scripts/run_exact_test_admin_lifecycle.py revoke --run-id "$EXACT_RUN_ID"
"$TEST_PYTHON" -B scripts/run_exact_test_admin_lifecycle.py recover --run-id "$EXACT_RUN_ID"
```

Prepare is **not read-only**: it writes a `prepared` audit after proving exact revoke SQL privilege/zero-row savepoint behavior, absence of custom triggers/RLS/inbound platform_admin FK hazards, zero admins and the effective request gate. It reads an existing exact Principal signed session only from `/run/enterprise-agent-test-exact-admin-v1/session.token` (regular 0600, <=4096 bytes, trusted ancestors), probes fixed `http://127.0.0.1:18100/api/v1/internal/test/exact-admin-capability` with the run header, no proxy/redirect. No arbitrary URL or new/shared credential is accepted.

Grant -> existing `platform_admins` exact membership + `execution_events` chained audit in one transaction. Original users.role stays member. Gate -> existing signed session/current credential version + `X-Exact-Test-Admin-Run-Id` + live owned lease -> unchanged formal catalog API. Exact allowed methods/bodies:

| Method | Route relative to `/api/v1/platform/agents/{exact-agent-id}` | JSON body |
| --- | --- | --- |
| GET | empty suffix | none / `{}` |
| POST | `/versions/{exact-revision-id}/test` | `{"configuration_fingerprint":"<exact-approved-fingerprint>"}` |
| POST | `/versions/{exact-revision-id}/publish` | `{"mode":"production"}` |
| PUT | `/instances/{exact-tenant-id}` | `{"agent_template_version_id":"<exact-revision-id>","overrides":{}}` |
| POST | `/instances/{exact-tenant-id}/enable` | none / `{}` |

The publish body's legacy `mode=production` denotes formal revision publication scope **inside the Test DB**, not Production environment access/deployment authority. Publish/configure/enable require independent publication approval and persisted real quality PASS. These HTTP writes and ordinary Chat execution are future isolated validation steps only; no real Runtime Test was executed here.

Runtime path: formal catalog POST -> real `AgentRuntimeTest` -> TaskService/AgentService -> real executor terminal handling -> `agent_template_tests` PASS/FAIL + associated Task/`run_traces` -> unchanged quality evaluator -> formal Publish/Enable. Candidate middleware installs `NativeSuccessor.verify(for_execution=True)` in existing Runtime isolation and TaskService pre-execution callbacks. Worker imports main's same TaskService. START permits first queued/running test without requiring a previous PASS; result validation and publication qualification remain separate.

Persisted Runtime validation entry is the complete `status` CLI above; it collects an actual consistent read-only snapshot and verifies exact authorized tests/Task/Run/actor/revision/evidence, then calls the pinned current parent Guard. Failures remain legitimate retained records, never publication eligibility. Each operation's minimal Task/Test IDs and scoped effect hashes are chained in existing audits, not secret/config/model response text.

Revoke -> exact owned membership only -> original permission state verified -> `active_test_platform_admin=0`; repeated revoke is safe. Foreign admin is preserved and FAIL CLOSED. In-flight operations block ordinary Revoke. Recovery requires independent fresh ROOT `dead-operation-proof.v1.json`, exact keys:

```text
contract run_id application_source application_tree last_audit_sha256 tickets
quiesced_process_ids observed_at independent_approval
```

It binds current last audit/tickets/exact started PIDs, age 0..60 seconds, independent string `EXACT_API_PROCESS_QUIESCENCE_ATTESTED_BY_06`. No caller boolean, fake proof or unsealed SQL substitute. 06 must actually stop admission and establish quiescence under separate authority before installing this proof. Cleanup remains available after lease expiry, but Source/Scope and ownership remain checked.

Recovery-mode read-only full-entry probe, from the same exact checkout/environment:

```sh
"$TEST_PYTHON" -B -c 'import json; from scripts.wechat_runtime_native_successor import NativeSuccessor; print(json.dumps(NativeSuccessor().verify(recovery=True), sort_keys=True))'
```

Recovery read is not cleanup/permission to restart; it does not delete membership or issue the quiescence proof. Normal `status` must PASS after cleanup with zero admins and no in-flight operation before qualification of restart.

## 06 complete isolated acceptance / ungranted qualifications

1. Fresh read-only attestation: actual UID, environment, Application/Tooling identity, current parent code/policy/approval, Schema/table set, protected keys, encrypted-record **metadata**, witnesses and budgets. Missing evidence stays `PENDING_06_READONLY_SNAPSHOT`; do not guess.
2. Use independent isolated PostgreSQL/Redis/logs, namespace/fixed-path layout and approved dependency/native service parity. Prove no live endpoints/storage/Production reachability and zero external calls. ROOT seal placement/systemd changes/fixture writes require explicit separate authority.
3. Construct independently approved fresh exact Source/tree/code/map/scope/policy files. Do not mint valid approval by merely running Candidate code, overwrite old seals or repin current data as initial anchors.
4. Execute the **whole `status` path**, including every transitive root import, actual PostgreSQL snapshot/Schema representation, unchanged Config/Terminal witnesses and fake encrypted metadata. Test missing/tampered file, symlink/owner/mode drift, dirty Source, wrong Source/tree, scope/run/tenant/principal/Agent/revision/schema/port/role, extra protected rows and forged audit. All negatives must reject.
5. Revoke-before-Grant proof on native PostgreSQL: exact row-delete and audit privileges, lock/transaction rollback, custom-trigger/RLS/FK rejection and capability/session identity; Grant failure/interruption recovery, foreign membership preservation, expiry and repeat cleanup.
6. Full signed formal API -> queued/running/PASS/FAIL -> Task/Run association and Worker-before-response race. Use isolated Runtime double/no network for qualification; any real Provider/model test remains separately budgeted and authorized. No manual positive PASS.
7. FAIL/no evidence/forged PASS cannot Publish; independent approved PASS path can formally Publish/Configure/Enable, ordinary member Chat, then Revoke. Reject other platform routes, cross-Tenant/User/Revision, missing/forged Run header, original-role changes and post-revoke privileged operations.
8. Prove all current Config/Secret-v1 metadata, Terminal Task witness, Receipt V2/canonicalization/seeding and unrelated protected data remain checked. Do not modify real config, old receipts or CREATE_DRAFT seal.
9. Safe startup/restart/recovery **in isolation** with retained Runtime records, original member permission and final zero admins; verify API/MCP/Worker/queue services and guarded callbacks. Pure repeated snapshot reads in the 330 tests are not actual restarts.
10. Only after the whole-entry acceptance, controller review and a new exact Test successor/active/recovery identity may separate PRIMARY installation authority be requested. No such authority exists in this publication.

Old d8a may have its attested pre-Runtime baseline, but after new Runtime/Publish/Enable/admin audit persistence, direct old-Guard restart/rollback is **NOT QUALIFIED / MUST BLOCK**. Revoke does not remove retained evidence or restore old static compatibility. Require a reviewed compatible recovery successor; never delete tests/business data, alter config/Secret, use temporary SQL or rewrite historical seals to force rollback.

06 already completed `PRIMARY_PYC_EXACT_CLEANUP_COMPLETED`; `PERSISTENT_INSTALLED_SOURCE_HASH=CLEARED` for its attested predecessor. No historical PYC handling here. Every future Python service/probe retains `-B`/env=1 and full exact source verification.

`PRIMARY Changes=0` · `Production Changes=0` · `real Admin/Runtime Test=0` · `Provider/WeChat/Image Calls=0` · `PRIMARY SSH attempts=0`.

STOP after source publication and handoff. No deployment or live execution.
