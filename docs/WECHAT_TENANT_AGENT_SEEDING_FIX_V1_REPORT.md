# Tenant Agent Auto-Seeding Minimal Fix V1

Date: 2026-10-08. Result: `WECHAT_TENANT_AGENT_SEEDING_FIX_READY` (offline code readiness, NOT a live startup/Native Guard acceptance).

## Source and change boundary

- Business base: `db8e23658baa6e4b707380e178aded561d3280f2`.
- Base tree: `04b6774d9e7a95e87871ea9c2e360805988a8e13`.
- Successor branch: `codex/tenant-agent-auto-seeding-fix-v1`.
- This report is part of the single direct-child fix commit. `SEEDING_FIX_SOURCE` and `SEEDING_FIX_TREE` are its Git commit/tree, supplied by the final publication receipt; embedding either into its own tree would be self-referential.
- Lineage retained: db8 -> cb17abb (Tenant Secret) -> dca318d (Canonicalization) -> 4ca2133 (SKILL_ONLY_TEST_QUALIFIED) -> 0bfded6 (Controlled Action) -> b0e96df (Skill Dispatch) -> e7e96b1 (Skill Python Runtime).
- Isolated managed worktree used; the main workspace's seven pre-existing tracked changes and historical untracked files are excluded and preserved.

Changed files:

1. `enterprise_agent_poc/app/product_store.py`: four added lines obtain approved exclusions and skip only those tenants in the legacy Instance insertion loop.
2. `enterprise_agent_poc/app/test_tenant_seeding.py`: read-only native Test-authority/Provision-evidence adapter.
3. `enterprise_agent_poc/app/settings.py`: one Test-only server configuration name in the existing safe runtime-config fingerprint list.
4. `enterprise_agent_poc/tests/test_tenant_agent_seeding.py`: isolated deterministic fixtures and optional explicit-source Native Guard graph proof.
5. `enterprise_agent_poc/scripts/verify_tenant_agent_seeding.py`: focused offline verifier.
6. This report.

No Skill, Secret Backend, UI, Agent execution/Runtime, Migration, database schema, Production release tooling, Native Guard implementation, existing sealed contract or Provision write-range change.

## Confirmed root cause

In db8, both ProductStore initialization paths unconditionally call `_seed_agent_catalog()`. It first initializes the three legacy Catalog definitions, then selects every Tenant and inserts enabled `campaign-agent`, `copywriting-agent` and `image-agent` instances with `ON CONFLICT DO NOTHING`. The API's `bootstrap_demo_data=false` only suppresses additional demo seeding; it does not suppress this loop. Provision itself is not the writer of these instances.

The fix preserves Catalog upserts and their existing `definition_source='legacy'` guard. It filters only the Tenant Instance insertion loop. It does not update, delete, disable or repair any Instance; existing productized definitions, revisions and Skill/Tool bindings are untouched. An already contaminated Synthetic Tenant still fails the unchanged Native Guard and must not be made to pass by manual deletion.

## Protected Test-only decision

This is an exclusion from implicit enablement, not permission to enable/use an Agent and not another Tenant registry.

Activation requires all of the following:

- Server environment `APP_ENV=test` and `ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED=true`.
- PostgreSQL DSN and actual database identity both exactly `127.0.0.1:55432`, database/role `enterprise_agent_test`. The actual-address query uses `host(inet_server_addr())`; there is no CIDR string trimming or hostname-alias widening.
- Fixed native authority root `/etc/enterprise-agent-test-tenant-seeding-v1`, with root-owned, root-group, read-only files, no symlink or group/world-writable ancestor.
- Approval bound to the running exact Git Source/tree via the existing `git_identity()` mechanism, which rejects dirty tracked executable application code.
- Root approval pins the raw SHA-256 of each new Provision Scope and receipt. Receipt paths must remain under `/opt/enterprise-agent-workbench-test/release-evidence`; receipt files must be read-only and root/service-owned, with no writable foreign ancestor.
- Scope identifies the exact Tenant and run, Test environment, matching authority and application Source/tree, purpose `WECHAT_PERSONAL_CENTER_CONFIG_V1`, zero WeChat/Provider/Image budget, and the unchanged three-object range `tenants`, `enterprise_configs`, `users`.
- Receipt is `PROVISIONED`, matches the canonical Scope SHA, run/Tenant/user, application and tooling Source/tree, Test DB identity and created-record tables, and reports zero workspace/Agent enablement/network calls.
- The current Tenant row's canonical SHA equals the receipt's exact created-Tenant row SHA. A Tenant absent after authorized exact cleanup is harmless; no deletion is performed by startup.

The Tenant's name, editable enterprise config, display label, ordinary request parameters and a bare environment flag are NOT sufficient evidence. No Tenant name is hardcoded. Invalid enabled policy raises only `TEST_TENANT_SEEDING_POLICY_BLOCKED`; no credential or private filesystem/database exception is exposed. Validation occurs before this method's Catalog/Instance seeding; existing initialization DDL is unchanged.

Production/development return an empty exclusion set before reading any Test files, including when the Test flag is present. Their previous legacy initialization behavior is preserved. Test without the server flag retains legacy behavior for existing local/demo fixtures; it is NOT an accepted Synthetic-test startup configuration. 06 must pin the flag to `true` in its new protected native configuration/identity contract and must not start the provisioned Synthetic Tenant with the flag absent/false. The flag's fingerprint name is declared in the existing safe config snapshot.

## Native declaration shape (operator-approved installation only)

The adapter accepts `/etc/enterprise-agent-test-tenant-seeding-v1/approval.v1.json` with exactly these keys:

| Field | Required binding |
| --- | --- |
| `contract` | `TEST_TENANT_AUTO_SEEDING_EXCLUSIONS_V1` |
| `authority_id` | New approved successor authority, shared with Scope |
| `environment` | `test` |
| `source_commit`, `source_tree` | Published fix's exact Source/tree |
| `database` | `{address: 127.0.0.1, port: 55432, database: enterprise_agent_test, db_role: enterprise_agent_test}` |
| `production_deploy_authority` | `false` |
| `budget` | Integer zero for `wechat`, `provider`, `image` |
| `exclusions` | At most 32 exact Scope/receipt bindings |

Each exclusion has only `scope_file`, `scope_sha256`, `receipt_file`, `receipt_sha256`. Scope is a versioned basename (`*.vN.json`) in the same fixed native root, not a reference to an old authority directory. Receipt is an absolute bounded-evidence-root path. Source/tree and digest format, path traversal, ownership, read-only status, inode consistency and file-size bounds are checked. The exact Scope/receipt metadata supplements the existing controlled Provision evidence; no trusted artifact has been installed/generated for live use in this task.

## Deterministic verification

All commands run locally with isolated temporary SQLite fixtures, fake native authority/PG identity responses, synthetic credentials and mock transports. No PRIMARY database or remote service is accessed.

| Verification | Passed | Failed / errors / raw skips |
| --- | ---: | --- |
| Seeding verifier: 29 discoverable cases + 1 explicit-source Native Guard graph proof | 30 | 0 / 0 / 0 |
| Skill-only qualification / Controlled Action / Task-MCP Dispatch | 91 | 0 / 0 / 0 |
| WeChat personal config regression | 23 | 0 / 0 / 0 |
| Tenant Secret provisioning regression | 60 | 0 / 0 / 0 |
| Skill Revision canonicalization regression | 30 | 0 / 0 / 0 |
| Total suite-case executions (some inherited fixture scenarios are reused) | 234 | 0 / 0 / 0 |

Verified cases include Catalog initialization, approved Synthetic Instance count zero, three executions of the actual API lifespan function's AST with the real ProductStore fixture, existing enabled/disabled Instances unchanged, original Production/development behavior, unregistered Tenant not silently exempt, missing/drifted/stale authority fail-closed, malformed/foreign Scope and receipt rejection, Tenant row identity drift, exact DB target rejection, native file ownership/mode/path/inode checks, existing productized definitions/revision bindings unchanged, real offline PREPARE completion, and ordinary Chat still rejecting a missing Instance.

The API lifespan test stubs Skill registry/runtime dependencies and uses SQLite; it is not an HTTP/native-systemd/real-PostgreSQL startup acceptance. The PostgreSQL identity query is tested with a fake connection. POSIX ownership checks are tested with fake stat/OS boundaries over harmless local files; Linux installation acceptance remains with 06. No Full pytest, Stage 2 or Candidate Build was executed locally/WSL.

The verifier requires an explicit read-only existing guard source, parses only its `graph` AST and supplies synthetic dependencies. It does not import/execute its live module setup, snapshot or DB access. Its real graph function accepts the zero-Instance synthetic graph and rejects one injected Synthetic Instance with `UNAPPROVED_SYNTHETIC_TABLE:tenant_agent_instances`.

Existing guard-source SHA-256 before/after:

`b8ec7ac7a9a2569ec4b49beabd18fa7af0aa6e69e4a4bae1217f31dcde56f8cc` — unchanged. This is offline graph evidence, not a fresh live Native Guard PASS.

Reproduction (from `enterprise_agent_poc`, without importing Settings/.env):

```text
python -B scripts/verify_tenant_agent_seeding.py --native-guard-source <read-only existing db8 guard common.py>
python -B scripts/verify_skill_only_test_qualification.py
python -B scripts/verify_wechat_personal_config.py
python -B scripts/verify_wechat_secret_provisioning.py
python -B scripts/verify_skill_revision_canonicalization.py
```

## 06 handoff — new authority required before startup

Known prerequisite: the existing `81afb29e6dd0af3a80df85072df3a66923a4eb9d` minimal Provision tool's validation constants bind db8. It cannot validate a successor Scope by merely changing a filename or `application_source`. 06 must obtain an approved successor tooling/Scope identity for the new Source, preserving the same three-object write range. That separate identity rebind is not implemented or approved by this local fix. Do not overwrite the db8 Source, old tooling identity, sealed artifacts or reuse any revoked Scope.

1. Fresh live attestation; bind this commit/tree and new Test-only successor identity. Preserve old identities/evidence.
2. Install the exact new Source without any Production operation. Use newly approved minimal Provision tooling/Scope bound to this Source/tree, zero real-call budget, unchanged object range and protected native configuration with the required flag `true`.
3. Provision the new exact Synthetic Tenant via the approved tool; preserve/seal its receipt. Before API startup, install the root-owned read-only new seeding approval/Scope with actual raw file hashes and new Source/tree/authority, exact receipt and Tenant-row binding. Native approval is an operator action requiring its own reviewed identity, never supplied by an ordinary user request.
4. Start API; confirm Catalog present, Synthetic `tenant_agent_instances=0` after startup AND restart, existing Tenant Instances/bindings unchanged, and fresh unchanged-strict Native Guard PASS. Missing approval, wrong flag/fingerprint, old Scope or identity mismatch is a blocker, not permission to bypass the Guard.
5. Only within the NEW approved Scope continue Secret/API/MCP integration and UI acceptance. This report grants no real WeChat/Provider/Image call or Production credential authority.
6. Exact authorized cleanup through the new Provision receipt; verify zero unexpected business objects and STOP. Do not manually delete Instance rows to manufacture startup PASS.

## Safety receipt

`PRIMARY changes=0` · `Production changes=0` · `WeChat calls=0` · `Provider calls=0` · `Image calls=0`.

No live admin/Agent/credential provision, live dry-run/apply, deployment, service switch/restart or database mutation. `PRODUCTION_UNCHANGED`.
