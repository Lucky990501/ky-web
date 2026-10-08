# WeChat Persistent Config Native Guard Compatibility Fix V1 — local candidate

Date: 2026-10-09 (Asia/Shanghai). Customer target: 2026-10-10.

**State: LOCAL_IMPLEMENTATION_TESTED / 06_SNAPSHOT_SCHEMA_CHECKED / LIVE_CONFIG_TRANSITION_EVIDENCE_MISSING. NOT GUARD_FIX_READY.**

The subsequent controller instruction prohibits further PRIMARY SSH attempts and reserves real environment evidence for 06. During this turn 06 delivered its sanitized snapshot; the addendum below records actual field compatibility. It still does not establish config-transition authorization, current Native Guard acceptance or safe live restart.

## Identity and boundary

- Application base: `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`; base tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`. Initial tooling commit `dbc7b6c9912af2254ac94d40177c975963945c7d` has this direct parent; the snapshot regression follows as a separate normal commit, without rewriting either identity.
- Branch: `codex/wechat-persistent-config-guard-v1`.
- This is a separate Test-only TOOLING Source. Application code remains byte-identical to d8a; 06 must keep the actual application identity d8a, not install this tooling Git HEAD as an Application release identity.
- New Source/tree are supplied by final Git publication receipt; they cannot be embedded into their own tree without self-reference.
- No historical commit/Seal/Receipt/base pin is rewritten. No live policy is installed or approved by this candidate.

Only added files:

1. `enterprise_agent_poc/scripts/wechat_persistent_config_guard.py` — versioned structural/business overlay and observer/preflight interfaces.
2. `enterprise_agent_poc/scripts/verify_wechat_persistent_config_guard.py` — explicit-source offline fixture verifier.
3. `enterprise_agent_poc/scripts/audit_wechat_persistent_config_readonly.py` — dormant, opt-in 06 metadata audit; no credential fallback.
4. `enterprise_agent_poc/tests/test_wechat_persistent_config_guard.py` — signed-session API/DB/synthetic-codec fixtures and old Guard AST proof.
5. This report.
6. `enterprise_agent_poc/tests/fixtures/wechat_guard_readonly_snapshot_v1.json` — sanitized 06 Schema/key/audit metadata, with no row values or credentials.

Application, Runtime, UI, Skill, Secret encryption/backend, Schema/Migration, seeding/Receipt V2 protocol and Production tooling are unchanged.

## Source-audit findings (not live facts)

Read the local d8a native `common.py` reference and d8a Application implementation. Native graph compares each protected `enterprise_configs` full raw-row fingerprint to `predecessor-live-protected.v1.json`, including Tenant ID, serialized `payload` and `updated_at`. Its special handling for the ephemeral Provision Tenant does not make a persistent Tenant's WeChat lifecycle mutable. The existing config table's whole-row lock therefore rejects legal API payload changes too.

Local d8a native reference SHA-256 before/after: `ef00794527f80363a06ba98d14d47a0f071e99f8c645f311585745545dd16810`, unchanged. This is the local common-cache reference, not the installed common.py. The actual installed identities supplied by 06 are recorded in the addendum; exact deployed-code integration remains unverified.

Source-declared PostgreSQL migration 001 defines `enterprise_configs(tenant_id TEXT PK/FK, payload TEXT NOT NULL, updated_at TIMESTAMPTZ NOT NULL DEFAULT now())`. Accepted Receipt V2 expects the exact three-column contract and timestamptz(6). These are expected contracts, NOT a claim about current live columns/types/triggers.

The formal profile API validates signed-session user identity, current Tenant, enabled DB account and enterprise_admin management role. Save locks the config row, calls the protected backend and updates only its payload. Empty AppSecret retains the old active Secret; rotation updates the version; revoke removes `wechat_account`. Test connection records server verification in the protected backend and emits a verification audit; frontend connected booleans are rejected.

d8a save/rotate/revoke audits contain only `contract, tenant_id, environment, provider, actor_id, version`. Verification contains `tenant_id, environment, actor_id, version, status, error_code`. They have no before/after config fingerprint or display-name/timestamp binding. Audit emission is also outside the config-update transaction. Backend status/version can corroborate a Secret reference, but cannot establish arbitrary config-field provenance. Source save does not update `updated_at`; any live trigger/other updater must be separately evidenced, never guessed.

Counterexample verified offline: after a real authorized save, change only `account_display_name` directly in the isolated DB. The old audit rows and Secret version remain identical. These sparse records cannot distinguish the extra unauthorized name write. A Guard that allows any account-shaped payload merely because a version-1 audit exists would violate FAIL CLOSED.

## Versioned local implementation

Guard contract: `WECHAT_PERSISTENT_CONFIG_NATIVE_GUARD_V1`.
Business observation: `WECHAT_PERSISTENT_CONFIG_API_OBSERVATION_V1`.

A. Structural layer keeps the original historical raw pin and a complete original row witness that MUST hash to that existing pin. Exact Tenant, Test environment, application Source/tree, root-approved actor full-row fingerprints and all non-WeChat payload fields remain protected. Witness/pin cannot be replaced with the changed current row. The candidate uses the existing accepted Receipt V2 row codec for business state identity, not a new hash normalization algorithm.

B. Lifecycle layer requires separately ROOT-approved observations of an actual authenticated formal API operation: exact method/path, actor, Source/tree/Tenant, before/after complete config rows, original audit ID/full fingerprint, successful response AppID/name/Secret-version flags, and ordered non-replayed transition chain. Current row must equal the final observed V2 hash; unrelated field/Tenant/actor/audit changes reject. Current configured status must come from authenticated server API status; connected/failed states additionally require matching existing verification audit evidence. Cross-Tenant references reject before status resolution.

The root approval loader only reads its fixed new native context, root-owned/read-only/no-symlink versioned files and exact byte pins. Normal request JSON or frontend claims never create approval. Observer construction returns a candidate record, never writes/approves it. Its callback implementations must be independently sealed/reviewed operator code; the candidate callback interface is NOT a deployed observer, verified live integration or authorization to execute API mutations.

Only after successful lifecycle validation does an IN-MEMORY copy restore that one config row to its proved historical witness for delegation to the unchanged FULL Native Guard graph. DB data is never rewritten. Every other Tenant/table/audit/binding and Synthetic Provision object remains in the delegate snapshot. Any delegate failure propagates; the overlay alone is never native PASS.

The caller must supply actual ephemeral Provision Tenant identities; the overlay refuses to cover them. Native Guard remains responsible for strict Receipt V2 ownership/cleanup, synthetic graph and zero unapproved instances.

## Local test evidence

| Suite | Passed | Failed / errors / raw skips |
| --- | ---: | --- |
| New lifecycle/negative/preflight cases + exact old graph AST proof + 06 snapshot checks | 42 | 0 / 0 / 0 |
| Receipt V2 / canonicalization / actual fixture Provision and cleanup | 95 | 0 / 0 / 0 |
| Inherited seeding and synthetic guard | 30 | 0 / 0 / 0 |
| Existing Tenant Secret backend/API/MCP gates | 60 | 0 / 0 / 0 |
| Existing Personal Center config | 23 | 0 / 0 / 0 |
| Total suite-case executions | 250 | 0 / 0 / 0 |

Fixtures use real d8a signed-session authentication/profile API/DB authorization, isolated SQLite, synthetic independent Fernet keys and mocked WeChat credential tester. New cases cover save, name refresh, rotation, revoke, protected identities, observed time change, unauthorized config/audit/actor/time writes, cross-Tenant references, unknown authority, Source/environment mismatch, downgrade to mere frontend claims, legacy evidence gap, explicit separately authorized reauthorization, callback schema failure and mandatory full delegate. Revoke then three actual ProductStore initialization cycles pass offline; this is not a native live-service restart proof.

The time-change case uses an ISOLATED fixture trigger to model a separately approved DB-managed updater, not a product migration or an assertion that PRIMARY has that trigger. The 06 snapshot confirms no actual user-defined trigger; a separate regression verifies d8a save leaves the timestamp unchanged in that case. Actual timestamp serialization/precision remains unverified. The old graph AST proves raw config drift rejection and successful delegation after valid observed lifecycle normalization; only graph executes with synthetic dependencies, never the live module/snapshot/DB entry.

No Full pytest, Stage 2, Candidate Build, actual service startup, production validation or real WeChat request. App code is unchanged, so Production behavior is not rewired; no Production proof is claimed.

Reproduce from `enterprise_agent_poc`:

```text
python -B scripts/verify_wechat_persistent_config_guard.py --old-native-common <read-only exact d8a common.py>
python -B scripts/verify_tenant_seeding_v2.py --tooling-root <clean 85a1451 checkout> --native-guard-source <canonical-v2 common.py>
python -B scripts/verify_tenant_agent_seeding.py --native-guard-source <unchanged V1 common.py>
python -B scripts/verify_wechat_secret_provisioning.py
python -B scripts/verify_wechat_personal_config.py
```

## 06 read-only evidence boundary — no live READY

One metadata-only SSH attempt was rejected by authentication before server entry. No password/key was requested and no fallback used. Controller then explicitly prohibited further SSH; no retry occurred.

The received snapshot supplies Source/tree, native artifact identities, table keys/counts, column names/types/nullability, absence of user triggers, account/reference field names and sparse audit field/version/status metadata. Remaining actual-environment items are `PENDING_06_READONLY_SNAPSHOT`; only 06 may collect them under approved read-only scope, without exposing secrets or complete config values:

- Actual timestamp precision/defaults and snapshot/raw-row representation; the known three-column/no-trigger Schema must not be guessed further.
- Current reference Tenant/environment/provider match flags and authenticated Secret backend/server-status metadata only, not decrypted AppSecret/token/ciphertext/key.
- Exact relevant audit IDs/actor identity and row representation. The received snapshot already confirms no stronger before/after config binding in the two existing events.
- Registered persistent actor's independently approved identity provenance and structural Guard compatibility; never silently exempt a new user.
- Whether stripping only WeChat (with the proven original serialization/time witness) matches the existing historical pin; never repin changed current data.

The opt-in audit script is dormant without `--approved-06-readonly`. 06 must approve its exact code and target before use. It attempts a fixed Test target without password/env PG service/pgpass/TLS-key fallback, read-only repeatable-read transaction, short timeouts and sanitized output only; inability to connect remains blocked. It neither reads the Secret backend nor proves connected status. It must not be executed by this window again. Alternative sanitized snapshot collection remains 06's responsibility.

Schema/key/field-name comparison and three snapshot-specific regressions are complete. Further evidence still cannot substitute for missing genuine transition authority. Do NOT mark this candidate GUARD_FIX_READY or install it before the remaining identity, observer and recovery review.

## Read-only compatibility preflight and new successor

`preflight_existing_native(native, status_reader=...)` is a non-mutating adapter for 06's separately approved wrapper. It retains native load/env/source/schema/table-set checks, loads the new protected context and observation pins, validates persistent business lifecycle, then delegates full old graph checks. `status_reader` must be the authenticated formal server status adapter; no arbitrary submitted boolean, new credential provision, backend bypass, Source initialization or restart. The wrapper must pin/check old native code and new tooling Source/tree before import. This local callback interface is not claimed as installed live-safe tooling.

Absent actual authority/observation/witness produces FAIL CLOSED. A read-only current snapshot cannot mint an observation or retroactively prove a write. Root-owned observation approval and minimal registered persistent-Tenant/actor scope need a NEW reviewed Test successor bound to exact unchanged Application d8a AND the new independent Tooling Source/tree, old policy/pins and actual Schema. Old seals remain immutable; no wildcard Tenant/table allowance.

## Restart / rollback / protected data recovery constraints

**DIRECT OLD-D8A SEALED-GUARD RESTART OR ROLLBACK IS BLOCKED.** Offline source evidence proves a changed persistent config is rejected by its exact full-row pin; 06 now confirms the installed formal health path returns `EXISTING_FIXTURE_MUTATION:enterprise_configs`. Do not perform a live restart to discover failure. Five unchanged active service PIDs and connected/version-1 audit metadata are independently reported by 06, not observed through this window's own server access. A protected compatible successor/recovery path still needs separate approval and validation. Application initialization alone passing does not make its native gate/release restart safe.

For a legacy gap, a possible protected recovery is a NEW explicit `AUTHENTICATED_API_REAUTHORIZE_EXISTING_PUBLIC_CONFIG_ONLY` authority: after immutable witness/actor/Schema review, an actual authorized user uses the existing formal API to re-confirm the current public AppID/display name with AppSecret empty. The unchanged backend then keeps the existing Secret and version/connected state; the observer captures new affirmative API evidence and links the existing audit. This is prospective business reauthorization, NOT re-sealing current data as the initial Fixture. It is only a proposed path, NOT approved/executed here. Unknown non-WeChat/identity/legacy timestamp drift still rejects; no recovery token blesses it.

If 06 evidence cannot validate this path, keep FAIL CLOSED and obtain a narrower authority/architecture decision. Do not reset/delete config, revoke/rotate the real Secret, disable Guard checks, convert receipt/hash versions, rewrite a historical pin or carry unqualified business data into a sealed predecessor. Any later live install/restart/recovery requires separate authorization, exact attestation and compatible native successor; Production rollback rules are unchanged.

`PRIMARY restarts=0` · `PRIMARY DB/config changes=0` · `Real AppSecret changes=0` · `Production changes=0` · `WeChat/Provider/Image calls=0`.

## Received 06 snapshot — actual compatibility addendum

Received `WECHAT_GUARD_READONLY_SNAPSHOT_V1.md` from 06, snapshot `2026-10-09 01:01:08 +08:00`, final PID recheck `01:04:42 +08:00`, under `PRIMARY_TEST_READ_ONLY_SNAPSHOT_APPROVED`. A metadata-only fixture now preserves the exact sanitized names/identities, not row values or credentials.

- Application d8a/tree 6021 match the candidate's separate Application binding.
- Actual columns are exactly `tenant_id(text, NO)`, `payload(text, NO)`, `updated_at(timestamptz, NO)`; no user Trigger. Expected shared V2 column names/types agree. Snapshot does not report temporal precision/raw timestamp representation; do not invent those values.
- Actual installed common SHA is `a40e35dc184746e7ba2f31a32e49054169c113645175aa98a455b26cdca72b09`, not the local common-cache AST reference. Policy SHA `f0cafe7bc1e85407da355febf1a855eb87c97036b5c0e094a250881381a9a028`; approval SHA `6c3f1d239011399f4784f0215d1ec49f7b3aed247cd00aee0e05555ea376a460`; original fixture SHA `90814d071f11d484491b028a8d95b676c7a95dbda89b2dc639cded8de64943f5`. No substitution of local code hash for actual installed identity is allowed.
- All 12 protected tables and their keys are recorded in the fixture: tenants, users, enterprise_configs, agent_templates, agent_template_versions, agent_template_version_skills/tools, skills, skill_versions/packages, tenant_agent_instances, platform_admins. They must remain covered by the unchanged full structural delegate.
- Actual account/reference fields and both event payload field sets exactly match source/fixture expectations. The two observed events prove actor-field presence, Secret version 1 and connected outcome; they record no config before/after or field digest. Encrypted Secret was not opened by 06. CONNECTED audit is not row-mutation provenance.
- Actual formal health is `BLOCKED / EXISTING_FIXTURE_MUTATION:enterprise_configs`. Five service PIDs remain identical and active. This is direct evidence against claiming old d8a sealed-guard restart/rollback qualification.
- Three additional local tests verify actual Schema/name compatibility, no-trigger d8a save preserving timestamp, and fail-closed reproduction with the actual sparse connected event shape. New suite 42, aggregate 250 case executions, 0 failed/errors/skips.

Remaining blockers: exact installed code integration/wrapper/row snapshot representation, historical row witness and immutable actor identity bindings, approved live observer implementation/new Test successor, and trustworthy config-transition/recovery evidence. Existing protected hashes alone do not supply a historical row witness; a proposed witness MUST match the immutable fixture, never today's changed row. No observation/recovery receipt has been minted or installed here.

The candidate is published for review, not authorized to apply. Real current config compatibility remains FAIL CLOSED, `LIVE_CONFIG_TRANSITION_EVIDENCE_MISSING`; do not announce `WECHAT_PERSISTENT_CONFIG_GUARD_FIX_READY`. No additional SSH attempt or PRIMARY action followed the controller instruction or snapshot receipt.
