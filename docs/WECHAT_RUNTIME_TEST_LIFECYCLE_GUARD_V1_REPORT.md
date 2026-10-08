# WeChat Formal Runtime Test Lifecycle Guard — local compatibility draft

Date: 2026-10-09, Asia/Shanghai. Customer acceptance target: 2026-10-10.

Status: `WECHAT_RUNTIME_TEST_GUARD_FIX_BLOCKED`.

Unique FIRST_FAILURE_POINT: `FORMAL_ADMIN_GRANT_REVOKE_API_UNAVAILABLE`.

This is a reviewed local component/contract draft, NOT a deployable Native Guard successor, formal Provider quality PASS, or startup/recovery qualification. No PRIMARY access or installation was performed. The existing live guards still reject Runtime Test, Publish/Enable and temporary-admin state. Local tests must not be used to claim those live rejections are fixed.

## Exact baseline and received evidence

Tooling parent: `7af76f1a27cc15d197566835f57f4b7fd6dc1ee7`; tree `765a0621edef321a90f02d9fbfb366168e179f55`. New branch: `codex/wechat-runtime-test-lifecycle-guard-v1`. Final new Source/tree are provided by the publication receipt; embedding a commit's own tree in its contents is not possible.

Application remains byte-identical to `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`, tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`. This Tooling Git HEAD is NOT a replacement Application Source.

The complete `WECHAT_RUNTIME_TEST_GUARD_READONLY_SNAPSHOT_V1.md` supplied by 06 was read and SHA verified:

`0e778a2279f4973cd519711e9b69bf4c6f2c281194e55fabf783e04bdf5a7465`.

It attests the CURRENT active overlay, not merely a historical Terminal Witness:

| Artifact | SHA-256 |
| --- | --- |
| Current `primary_guard.py` | `5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47` |
| Current Policy | `a06046de3ee1f2cdcc202d4785889ccadebcb417dd16d92ae266359111078604` |
| Current Approval | `4f6a0d5bcc5919d53508602bb7f0203d742d39e60d797314855144da233fd1c7` |
| Current Config Witness | `97f8334553be8a05fc07fb48b43ee0fa72ce36a1ff779b8ea85dc74dac20eee0` |
| Terminal Task Witness | `2558297098dddb39eebeefc9e7f0fd4707f42e9f3affe9b5494c752e2b818bb2` |
| Imported DCA graph | `0e079b0fd87db36be8efadff63ce129e90e1a5e20dfa27cf4228a83fd1e90d6b` |
| Imported d8a seeding successor | `a40e35dc184746e7ba2f31a32e49054169c113645175aa98a455b26cdca72b09` |

The local overlay/Policy/Approval/two witnesses/DCA copies match those actual identities. They are read/hash-only inputs to regression, not rewritten or imported as live modules. The known older local d8a common-cache copy is NOT substituted for the actual seeding import. Its tests are separately labeled historical/component regression.

Actual Runtime Test columns are `id, agent_template_version_id, configuration_fingerprint, test_type, task_id, status, result_json, created_at`, with status domain `queued/running/passed/failed/invalidated`. The actual Run table is `run_traces`, NOT `runs`. Task→Run has no DB FK, so exact application joins remain mandatory. A metadata-only fixture records this snapshot; no full business/config/credential row is copied.

Remaining environment details are `PENDING_06_READONLY_SNAPSHOT`: exact imported d8a successor bytes/full native snapshot representation and source manifest, native Python cache bodies/header metadata and fixed startup/cache environment. Snapshot identities/names already supplied are not relabeled unknown. Lack of these items never relaxes checks.

## Root cause and minimal component implementation

Actual imported DCA `graph()` universally requires EVERY test row to be `validation`, the one draft Revision and `task_id IS NULL`, otherwise `NO_RUNTIME_QUALITY_EVIDENCE`. That is a pre-productization contract, not a missing PostgreSQL feature. It rejects the first queued formal Runtime Test and both terminal outcomes. Actual overlay also pins `agent_template_versions`, `tenant_agent_instances` and the empty `platform_admins` baseline, so Publish, Enable and Grant have separate structural conflicts.

New component `scripts/wechat_runtime_test_lifecycle_guard.py` separates:

- **A — structural execution admission:** Test environment, exact Application Source/tree, Tenant, enabled actor full-row identity, stable Agent slug/ID, draft Revision/fingerprint, exact published Skill Revision/hash/model, configured Instance, current static validation, admin presence and bounded test count. No previous Runtime PASS is required. Returns no Provider/network authority or Publish eligibility. Formal signed-session API, real Codex type, queue, actual readiness/model and separate trusted admin provenance checks remain required.
- **B — persisted-result checks:** exact Test→Task→context→Instance→Run→Conversation/owner relations, Tenant/actor/Agent/Revision/model agreement, genuine runtime-test context rather than Skill-only qualification, state representation, immutable test identity and legal transition edges. Worker PID and formal result fields must agree with the d8a executor's derivation. PASS additionally needs actual persisted response equal to the Run final result plus its assistant message. Enqueue failure, interrupted-worker failure and pre-Run failures have narrow separate failure branches. Failed new Runs can legitimately lack an owner because d8a writes ownership in successful final persistence; any conflicting owner still rejects. A completed Task awaiting `finished()` remains NOT test PASS.
- **C — publication qualification:** independent publication authority AND valid B PASS AND the unchanged `ExecutionResolver._runtime_passed()` query. No write, fake PASS or bypass of formal publish/configure/enable APIs. The component does not grant live publication authorization.

Scope is supplied only by a separately sealed operator wrapper, not an HTTP request. The local draft does not mint root approval, hide rows from the current graph, exempt a table, create per-Task/Run witnesses, install a native adapter, or silently normalize published rows/admin grants to a sealed baseline. `live_preflight()` explicitly refuses an unqualified integration. This is deliberate FAIL CLOSED, not a completed Native Guard implementation.

The frozen Revision's fingerprint cannot silently change. An `invalidated` client flag without genuine configuration change rejects. A future independently approved revision-edit/recovery scope is needed to permit and prove fingerprint changes; the current draft does not invent that authority.

## Administrator lifecycle — why full compatibility is blocked

d8a exposes `SkillRegistry.grant_platform_admin(user_id)` and the formal operator script `scripts/grant_platform_admin.py`. It inserts membership only, has no Grant audit or expiry contract, and exposes no `revoke_platform_admin` method/API. HTTP Productization routes require platform_admin, but provide no Grant/Revoke route. Ordinary member role remains separate from membership and must not be upgraded.

The local fixture uses the EXISTING registry Grant solely to authenticate the signed-session API test. This is NOT evidence of a controlled live Grant→Use→Revoke lifecycle. No positive test deletes membership with SQL or relabels DB cleanup as formal Revoke. Fake caller-provided grant/revoke callbacks explicitly reject. `ACTIVE_TEST_PLATFORM_ADMIN=0` cannot be certified for an unimplemented live cleanup path; no live Grant was made here.

To close this point while preserving formal API semantics requires either an independently approved existing formal control-plane Grant/Revoke artifact not yet supplied, or a minimal separately versioned Application successor adding a Test-only exact-identity, authenticated/audited Grant/Revoke capability (including expiry, cleanup after expiry and final zero-admin verification). d8a alone does not offer it. No such Application mutation or permission-policy expansion was silently introduced in this Guard-only draft. This necessity must be reviewed before new Application identity approval; do not solve it with direct DELETE/UPDATE, a permanent admin, arbitrary callbacks, role changes, or an unsealed scope.

## Complete Productization static write boundary

| Operation | Actual write surfaces | Qualification / remaining native conflict |
| --- | --- | --- |
| Grant/Revoke | `platform_admins`; trustworthy permission audit needed | Empty pin conflicts; formal audited Revoke absent |
| Validation | `agent_template_tests` validation rows | Existing formal validation/fingerprint; NOT Runtime PASS |
| Runtime Test reservation | configured `tenant_agent_instances`, `agent_execution_contexts`, `tasks`, `task_agent_contexts`, `task_events`, runtime `agent_template_tests` | Exact formal admission; atomic Task/Test creation in ProductStore |
| Worker/Run execution | Task/status/events, `run_traces`, `conversations`, `conversation_agent_contexts`, `execution_events` | Source-owned runtime; exact Task/Run/context identities |
| Successful final persistence | `task_results`, `messages`, `conversation_owners`, Task/Run status, credits and optional artifacts | Real final response/associated evidence; no fake completion |
| Failed execution | Task/Run errors/status, Runtime Test result | Retained FAIL never gives publication eligibility |
| Publish | `agent_template_versions` status/scope/time, `agent_templates` pointer/lifecycle/time/actor | Independent publish authority + static validation + real B; static native pins still conflict until adapter review |
| Configure/Enable | `tenant_agent_instances` revision/overrides/status/time | Published current Revision + Runtime PASS + bindings/readiness/model; never direct enabled edit |
| Ordinary Chat | immutable context/maps, Task/Run/conversation/result/events, credits | Same Tenant ordinary member, enabled current published Revision, current quality |
| Release-owned path, when actually applicable | `agent_release_operations`, `agent_release_artifacts`, `agent_release_events` | Owner/source/manifest seals, runtime-validation linkage and allowed state; not assumed for an existing ordinary Agent |

Both successful formal Runtime Test and ordinary Chat use d8a's normal `credit_accounts/credit_transactions` charge path with the context's exact Agent cost; no zero-cost exemption is assumed. Optional image/document/tool artifacts depend on real bound behavior and are NOT newly authorized by this task. No broader Agent/Skill/Runtime capability was modified. Release-owned PostgreSQL lineage is statically audited, not executed in the SQLite fixture; do not claim a release-provenance E2E PASS.

## PYC evidence and strict proposed compatibility

06 fresh full preflight fails `PERSISTENT_INSTALLED_SOURCE_HASH` because exactly seven extra `app/__pycache__/*.cpython-311.pyc` files appeared. Approved files missing=0; approved file hash mismatches=0; Git Source/tree exact. This proves no reported sealed Source-file change; it does NOT prove every cache code body is legitimate.

New read-only `scripts/wechat_installed_source_cache_guard.py` retains ALL old sealed file hashes, rejects missing/changed source, symlinks and every unrelated extra file. Only the seven exact observed module cache paths are eligible. Under pinned native CPython 3.11/optimization=0, it validates interpreter magic, header flags, standard timestamp/size or source-hash header, original source filename and exact marshal equality with code recompiled from the already-approved source. Code is NEVER executed; unexpected/forged/incompatible bytecode rejects. No blanket `.pyc` filter, deletion or manifest rewrite occurs.

Its 11 local synthetic-cache tests use installed CPython 3.13 solely for algorithm regression. They are NOT fresh 3.11 validation of the real seven caches. Native cache data is still pending 06 read-only validation.

Recommended later protected startup contract: keep d8a source sealed; set one reviewed `PYTHONPYCACHEPREFIX` outside the immutable release and `PYTHONDONTWRITEBYTECODE=1` for EVERY API/MCP/Worker/preflight import process. `-B` alone stops writing but does not prevent reading an existing in-release cache. Prefix must be outside source, separate Test-only trusted ownership/scope, and independently approved/checked in both active and recovery units. Existing release caches may remain; no deletion of running files is necessary. First qualify the exact seven caches or reject them; never silently expand the allowlist to future caches. These are proposals, not changes to PRIMARY.

## Deterministic test results

| Suite | PASS | Failed / errors / raw skips |
| --- | ---: | --- |
| Formal lifecycle/API/executor + cache + exact snapshot/identity + imported DCA graph | 52 | 0 / 0 / 0 |
| Persistent Config component / historical graph regression | 42 | 0 / 0 / 0 |
| Tenant Secret backend/API/MCP gates | 60 | 0 / 0 / 0 |
| Personal Config | 23 | 0 / 0 / 0 |
| Formal Receipt V2/canonicalization/Seeding/cleanup | 95 | 0 / 0 / 0 |
| Inherited V1 Seeding/native synthetic guard | 30 | 0 / 0 / 0 |
| Total suite-case executions | 302 | 0 / 0 / 0 |

Positive test rows and PASS/FAIL are created by the real d8a signed-session catalog API → AgentRuntimeTest → queue callback → TaskService/AgentService → executor `finished()`, using an isolated marked SQLite DB and Runtime double. No positive Runtime Test row or PASS is manually inserted. Network is rejected except Windows asyncio's internal socketpair construction. This is deterministic code-level evidence, NOT a real Codex Provider execution, native Redis/systemd execution, real PG constraint parity or customer Runtime qualification.

Covered: first test with no previous quality; Task/Run identity; result persistence; failure blocks product publish; no record blocks publish; explicit independent publication flag plus unchanged product quality; actual formal publish/configure/enable and ordinary non-admin member Chat in isolation; forged PASS, malformed associations, wrong Tenant/actor/Agent/Revision/Skill/Run, missing final results, unfinished tests and terminal mutation reject; FAIL and PASS both retained across repeated nonmutating reads. Repeated fixture validation is NOT a service restart proof. Formal admin Grant/Revoke and native startup/recovery remain NOT IMPLEMENTED/NOT QUALIFIED, not skipped cases disguised as PASS.

The current Config/Terminal Witness file bytes are unchanged and re-hashed to 06 identities. Old Config and Receipt/Seeding regressions pass. Future exact current-native witness delegation after lifecycle transitions still needs an independently sealed adapter; file-hash preservation is not a fresh full native acceptance.

Initial test-harness failures (Windows internal socketpair denial, then overly strict failed-Run owner assumption) were corrected against actual code behavior and rerun. One inherited-verifier invocation used a nonexistent local reference path before test execution; the correct immutable V1 source subsequently ran 30 PASS. No failed test is omitted from final suite results or represented as a live result. No Full pytest, Candidate Build, Stage 2 or WSL validation.

## 06 handoff / safe startup and recovery

Do NOT install this draft as a service guard or call its component result full native PASS. First close the unique formal-admin interface gap and review the remaining adapters; no PRIMARY mutation is authorized by this report.

The future reviewed successor must independently bind exact Application and Tooling Source/tree, actual DB target/Schema, approved Tenant/actor/Agent/Revision/fingerprint/Skill/model and separate A/C authority. It must retain unchanged Config/Terminal Witnesses, all other protected rows, Receipt V2 ownership/cleanup/seeding, and independent workload budgets. It must validate complete lifecycle graphs rather than drop runtime-test rows or wildcard business tables. Grant expiry/revoke and final admin count zero need formal API/audit proof. Published/Instance changes need formal Productization origin and exact permitted field transitions, not just a compatible-looking final row.

06 read-only preflight is only through a NEW reviewed exact-code native wrapper: fresh identities and source/cache verification → full DB read-only consistent snapshot → actual authority/admin lifecycle checks → A/B/C checks plus all existing native invariants → approved active AND recovery contexts. `live_preflight()` remains fail-closed until this wrapper is qualified; no default remote connection or DB initialization exists.

Snapshot says five services are active, but fresh preflight fails even before adding Runtime Test evidence. **Do not directly restart or roll back to the incompatible current/predecessor guard.** Later startup/restart/recovery requires separate authority and qualified active/recovery successors with retained failed and passed records, exact cache policy, final zero admins and unchanged config/Secret v1. Offline fixture reads do not replace this proof. No old seal/receipt is edited, current business data is not repinned as a historical fixture, and no real config/Secret is changed.

`PRIMARY Changes=0` · `Production Changes=0` · `Provider/WeChat/Image Calls=0` · `PRIMARY SSH attempts=0`.

STOP. No deployable READY claim.
