# WeChat Tenant Agent Seeding Guard Receipt V2 Compatibility Fix

Result: `WECHAT_SEEDING_GUARD_V2_SOURCE_READY` — offline compatibility/source readiness only, not PRIMARY installation or live acceptance.

## Frozen Source and lineage

- Parent Source: `9e6daef89a0aa6bd10bb8788fbc73207d11dccd5`.
- Parent tree: `f7c5ec659f2ddc879eba4a0c2464f7e59d335f92`.
- Previous application: `db8e23658baa6e4b707380e178aded561d3280f2`.
- Branch: `codex/wechat-seeding-guard-v2-compatibility`.
- Single new commit must have 9e6 as its direct parent: db8 -> 9e6 -> new V2-compatible Source. Final publication receipt supplies `SEEDING_V2_SOURCE` / `SEEDING_V2_TREE` / remote MATCH; these cannot be embedded in their own tree without self-reference.
- Historical db8, 9e6 and dca commits are not rewritten. PRIMARY's dca state is an upstream fact; this task does not connect to PRIMARY to refresh it.

Changed files only:

1. `enterprise_agent_poc/app/test_tenant_seeding.py`: explicit V2 protocol/hash selection, exact V2 schemas, protected active-Scope binding and downgrade rejection.
2. `enterprise_agent_poc/scripts/receipt_row_canonicalization.py`: the accepted shared V2 module, byte-for-byte unchanged from formal tooling.
3. `enterprise_agent_poc/tests/test_tenant_agent_seeding_v2.py`: deterministic contract/integration fixtures and canonical identity tests.
4. `enterprise_agent_poc/scripts/verify_tenant_seeding_v2.py`: explicit-source offline verifier.
5. This report.

No ProductStore/Settings seeding-business change relative to 9e6; no Provision SQL, original Receipt V2 specification, Skill, UI, Secret encryption/backend, Personal Center API, Skill Dispatch, Migration, schema or Production release tooling change. Main-workspace user changes/historical files remain outside this isolated worktree commit.

## Actual accepted V2 reference

Read the clean, local formal tooling checkout, its complete V2 protocol implementation, scope validator, receipt construction/approval/cleanup functions and original tests. No live tooling entry was executed.

| Identity | Value |
| --- | --- |
| Formal tooling Source | `85a14513b6c863f8d506b86b3083a19d97b72799` |
| Formal tooling tree | `9683c996acacd0417d91d8e38700c0da413fc80e` |
| Minimal Provision contract | `TEST_ONLY_MINIMAL_PROVISION_V2` |
| Receipt row version | `RECEIPT_ROW_CANONICALIZATION_V2` |
| Exact cleanup authority | `EXACT_RECEIPT_OWNED_OBJECTS_ONLY` |
| Canonical module SHA-256 | `1c62c7805b473abfb906f88642c36d4e77e0a7a6c9537f31f60e1ff0b8aff09a` |
| Formal Provision/cleanup module SHA-256 | `9b60b5be33fa002c1216b5b55281101e6c528b770cea351c488700ed47594fc6` |
| Actual V2 Native Guard reference SHA-256 | `1a09103e8befd4ec7aaaf44ea3f5674662667cdd9ad63f90c2bf10e31b5ddfac` |

The exact reference files are `scripts/receipt_row_canonicalization.py`, `scripts/provision_test_tenant.py`, `tests/test_wechat_minimal_provision.py`, `tests/test_receipt_row_canonicalization.py`, `RECEIPT_ROW_CANONICALIZATION_V2.md`, and the existing canonical-v2 native `common.py`. Both the Git Source/tree and byte pins are checked by the verifier, not inferred from a version label. All reference source bytes are unchanged after verification.

## Confirmed incompatibility and fix

Frozen 9e6 accepts only Minimal Provision V1 and hashes a raw Tenant dictionary with `default=str`. V2 adds explicit `receipt_version` / `cleanup_authority`, and its row SHA hashes a version/table envelope containing every column in PostgreSQL ordinal order. Aware native datetime and PostgreSQL JSON timestamptz strings normalize to UTC with six microsecond digits; naive times and unknown/missing fields are rejected. Config JSON has deterministic key order, retains array order/nulls/numbers, and rejects non-finite values. This is not raw JSON hashing.

The new Guard dispatches only recognized versions:

- V2 Scope AND Receipt must declare `RECEIPT_ROW_CANONICALIZATION_V2`, exact formal field sets, exact cleanup authority and matching Source/tree/authority/run/Tenant/user/tooling/DB identities. Each of the three created-record lists must contain exactly one correctly identified, 64-hex-hashed row. The current exact Tenant row uses the unchanged shared `row_sha256('tenants', row, VERSION)`.
- The shared canonicalizer's exact accepted bytes and loaded module path are checked before use. Its existing repository `*.py text eol=lf` rule is unchanged. There is no second codec, string trimming, guessed normalization or fallback to raw V1 hashing.
- Existing protected file ownership, no-symlink/path checks, raw Scope/Receipt pins, running Source/tree, exact Test PostgreSQL target, zero-call budget and exact allowed-object range remain required. Ordinary API input/editable Tenant config cannot supply these declarations; wildcard Tenant identity is rejected.
- V1 remains compatible only with its historical V1 semantics and `.v1.json` declaration. V2 markers in V1 Scope/Receipt, V2 filename relabeling, unknown versions or V2 canonical hashes presented as V1 are rejected. Historical V1 evidence is not converted or edited.

The seeding-business loop is unchanged from 9e6: the approved exact Synthetic Tenant has zero enabled/disabled Instances added; `campaign-agent`, `copywriting-agent`, `image-agent` are NOT_CREATED. Catalog still initializes. Existing Instances/statuses and bindings are not deleted/disabled/repaired; normal Production/development startup does not read Test declarations or import the V2 codec.

## Active Scope / revocation binding

An archived/copy Scope plus pinned Receipt is not proof of current authorization. The native seeding approval at `/etc/enterprise-agent-test-tenant-seeding-v1/approval.v1.json` retains its existing top-level declaration contract and Source/tree fields. For V2 ONLY, an exclusion now has exactly five keys:

```text
scope_file          = provision-scope.v2.json
scope_sha256        = SHA-256 of exact protected Scope bytes
receipt_file        = exact bounded release-evidence receipt path
receipt_sha256      = SHA-256 of exact Receipt bytes
active_scope_file   = /etc/enterprise-agent-test-successor-<approved-new-identity>/provision-scope.v2.json
```

The declared copy remains in the existing fixed seeding authority root. `active_scope_file` must be the protected official Provision activation file: absolute, immediate child of an `/etc/enterprise-agent-test-successor-*` directory, exactly `provision-scope.v2.json`, root-owned/read-only with trusted ancestors and no symlink. Its complete parsed Scope and raw SHA must match the declared copy. Paths to archived `*.approved.v2.json`, foreign/user directories and missing/revoked active files fail closed. Removing the formal active Scope therefore blocks startup even while the declaration copy and Receipt remain available. The extra field is a read-only protected declaration binding, NOT a Receipt/Provision schema change or new permission.

Scope canonical SHA in Receipt is still the formal tooling's existing `digest(scope)`; the native raw file pin may differ due to the final newline. Tests explicitly verify this distinction. No credential path/content is resolved by Seeding Guard.

## Final deterministic tests

| Suite | Passed | Failed / errors / raw skips |
| --- | ---: | --- |
| Inherited Seeding + unchanged V1 Native Guard graph proof | 30 | 0 / 0 / 0 |
| Inherited qualification / Controlled Action / Task-MCP Dispatch | 91 | 0 / 0 / 0 |
| Inherited Tenant Secret provisioning | 60 | 0 / 0 / 0 |
| Inherited Personal Center config | 23 | 0 / 0 / 0 |
| Inherited Skill Revision canonicalization | 30 | 0 / 0 / 0 |
| Inherited subtotal | 234 | 0 / 0 / 0 |
| New V2 verifier: 5 shared-code identity + 32 cross-contract + original 58 accepted tooling fixtures | 95 | 0 / 0 / 0 |
| Final suite-case executions (inherited scenario code may be reused) | 329 | 0 / 0 / 0 |

Cross-contract fixture executes the exact pinned formal V2 `provision_minimal()` SQL and Receipt builder against an isolated temporary SQLite fixture carrying the complete PostgreSQL receipt field set, then validates every created row with the actual V2 codec. Only fake authority/application identity and fixture I/O/DB boundaries are substituted; original SQL, Receipt fields and hash algorithm are not rewritten. The produced Receipt's actual bytes match the pinned fixture used by the new Guard. The real ProductStore initialization and actual API lifespan function AST run with mock Skill-registry/runtime dependencies: Synthetic Instances stay zero after three starts; existing enabled/disabled Instances remain identical.

The same V2 Receipt is also fed to the actual frozen 9e6 Guard blob read from Git, using 9e6's original four-field declaration, and reproduces rejection; the new Guard accepts the correctly bound V2 path. Other cases reject raw/wrong row hash, wrong Tenant, Source/tree, environment, tooling identity, forged pins, unknown Scope/Receipt fields/versions, revoked active Scope with copy retained, archived/foreign activation paths, active byte-pin mismatch, missing V2 active binding, wildcards, downgrade attempts, changed microsecond and altered canonical source bytes. Equivalent timezone representations pass.

Actual unchanged V2 `cleanup_minimal()` verifies all receipt-owned rows, performs exact fixture cleanup, preserves the Receipt and unrelated Tenant data, is idempotent and blocks row drift/replay. Original 58 formal tooling cases additionally cover V1 evidence preservation, cleanup/config/timestamp drift and unreceipted business objects.

The existing V2 Native Guard's actual `graph` AST runs with synthetic dependencies/full canonical rows and still rejects an injected Instance with `UNAPPROVED_SYNTHETIC_TABLE:tenant_agent_instances`. Its live module setup/DB/snapshot are NEVER imported or executed. V1 Guard SHA remains `b8ec7ac7a9a2569ec4b49beabd18fa7af0aa6e69e4a4bae1217f31dcde56f8cc`; V2 Guard SHA remains the table pin above. There is no permission widening.

Evidence limitations: database fixtures are SQLite, not real PostgreSQL/native service acceptance; PG target identity and native trust are mocked. On Windows, only exact ephemeral fixture credential-file POSIX stat/getuid checks are emulated so original cleanup functions execute; this is reported as `EXACT_EPHEMERAL_WINDOWS_FIXTURE_EMULATION`, not native Linux permission proof. Ordinary test discovery has no dependency on local archived tooling; the explicit verifier supplies pinned formal-source cross-contract cases. No Full pytest, Stage 2, Candidate Build or live PRIMARY validation runs in this task.

Reproduction from `enterprise_agent_poc`:

```text
python -B scripts/verify_tenant_seeding_v2.py --tooling-root <clean 85a1451 formal checkout> --native-guard-source <unchanged canonical-v2 common.py>
python -B scripts/verify_tenant_agent_seeding.py --native-guard-source <unchanged original V1 common.py>
python -B scripts/verify_skill_only_test_qualification.py
python -B scripts/verify_wechat_secret_provisioning.py
python -B scripts/verify_wechat_personal_config.py
python -B scripts/verify_skill_revision_canonicalization.py
```

## Unchanged business boundaries

Git diff from 9e6 is restricted to the five listed files. Secret encryption/backend, Personal Center API/UI, Skill Dispatch and its committed integration permissions are unchanged. `integrations/skill-dispatch.v1.json` still lists only PREPARE enabled and CREATE_DRAFT disabled; the existing dispatch-config default is also `CREATE_DRAFT=False`. No real draft, credential provision, WeChat/Provider/Image request or Production action was performed.

## 06 handoff and rollback cautions

1. Use the published exact new Source/tree, verify fresh identity/file pins and formally bind the new Test Successor. Do not install this Source under db8/9e6/dca's old identity or edit sealed predecessor artifacts.
2. Keep `APP_ENV=test`, `ENTERPRISE_POC_TEST_TENANT_SEEDING_POLICY_REQUIRED=true`, exact Test PG target and zero-call budget in the protected/fingerprinted configuration. Missing/false flag is not an approved Synthetic startup configuration.
3. Fresh exact Scope/Receipt must use `TEST_ONLY_MINIMAL_PROVISION_V2` + `RECEIPT_ROW_CANONICALIZATION_V2` + `EXACT_RECEIPT_OWNED_OBJECTS_ONLY`. Bind application Source/tree to this new commit and tooling identity to the separately approved formal successor. The reference 85a tooling is historically db8-bound; do not relabel that sealed identity. Rebind the formal Test Successor/precise Provision Scope through the approved versioned mechanism without changing SQL, canonicalization or object range.
4. Seal the actual new Receipt and native declaration: copied Scope bytes plus raw pins and `active_scope_file` MUST reference the same current formal activation file. This replaces no old approval/Receipt; use new approved identities/evidence. Never point at an archived/revoked Scope. Retain the exact shared canonical module with the published Source.
5. Only then perform the authorized PRIMARY startup/restart checks: Catalog normal, Synthetic Instances=0, existing Instance/binding pins unchanged, strict fresh Native Guard PASS. Secret/API/MCP/UI steps remain separate 06 authority; this report grants no real network operation.
6. Cleanup/revoke through the approved V2 exact receipt-owned path, preserve immutable Receipt/Scope/evidence. Once the active Scope is revoked, a still-referencing declaration intentionally blocks restart; retirement of that protected binding needs the corresponding reviewed operator action, not reinstating an archived file.
7. This task performs/authorizes no live rollback. Any later rollback to dca/9e6 must first follow 06's authorized stop/revoke/exact-cleanup procedure and prove no scoped Synthetic Tenant remains: an older startup still seeds legacy Instances for it. Do not carry V2 authorization into an old Source, convert hashes/contracts to V1, reuse revoked scopes or manually delete Instances to manufacture PASS.

`PRIMARY Changes=0` · `Production Changes=0` · `WeChat Calls=0` · `Provider Calls=0` · `Image Calls=0` · `PRODUCTION_UNCHANGED`.
