# WeChat Runtime Test Admin Lifecycle Closure V1

Date: 2026-10-09 (Asia/Shanghai). Customer target: 2026-10-10.

Status: `WECHAT_RUNTIME_TEST_GUARD_FIX_BLOCKED`.

Unique FIRST_FAILURE_POINT: `NATIVE_SUCCESSOR_FULL_ENTRY_ISOLATED_VERIFICATION_NOT_PROVEN`.

The former `FORMAL_ADMIN_GRANT_REVOKE_API_UNAVAILABLE` has a local implementation and passing deterministic closure tests. The complete native entry, however, has not yet been executed in a fixture covering its root approvals, full installed-source map, imported d8a/current overlay, actual Schema snapshot and secret-record metadata together. Structural/component PASS must not be promoted to whole-entry startup/recovery qualification. This Source is reviewable, not authorized for PRIMARY installation or real Grant.

## Frozen identities and necessary Application successor

- Parent Tooling Source: `3d4f57a445fbdfa4b1761e29c56d06aea1f909a4`.
- Parent tree: `6dcf595975eebfc628e3a68e5c742270c71b14e0`.
- PRIMARY remains Application `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`, tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`.
- Branch: `codex/wechat-exact-test-admin-lifecycle-v1`.
- New Source/tree are supplied by final Git publication receipt. This commit contains both Tooling and the minimal Application permission hook, so the two successor identities are the same exact commit/tree. It must not be installed under the old d8a identity or seal.

Actual d8a permission contract is a global `platform_admins` membership boolean. It has Grant but no Revoke/audit, and does not constrain Agent, Revision, route or authorization Run. A Tooling-only allowlist would not prevent an already logged-in principal from bypassing Tooling and using unrelated platform APIs. That is the actual contract proof requiring the Application change allowed by the task.

Only Application changes: new `app/test_exact_admin_gate.py` plus three integration lines in `app/main.py`. Production returns before importing/reading Test authority; Test default flag=false retains prior behavior. No Runtime/Skill/Prompt/Secret/UI/Schema/Migration/Production permission capability is changed. API and Worker share the existing main TaskService; the required Test successor wires native isolation callbacks into the existing Runtime Test/Worker extension points, not a replacement executor.

## Exact permission implementation

`scripts/exact_test_admin_lifecycle.py`, contract `EXACT_TEST_ADMIN_GRANT_REVOKE_V1`, is the formal sealed Test permission service. It uses existing `platform_admins` and `execution_events`, not a second user/permission registry or new table.

Fixed native root `/etc/enterprise-agent-test-exact-admin-v1` contains separately ROOT-owned/read-only/no-symlink approval and scope. Scope binds Test, base d8a, exact successor Application/Tooling Source/tree, Tenant, existing member Principal/V2 canonical row hash, stable WeChat Agent/Revision/fingerprint, exact operations, one UUID Run and maximum one-hour expiry. The request cannot choose Tenant/User/Agent identity; all request identities must equal the registered root scope. All code pins and clean Git identity are checked before native use. No broad administrator management endpoint is exposed.

Before Grant, `prepare` requires the real fixed-loopback API capability response for that same signed Principal, scope Run and successor identity. A caller boolean or arbitrary callback does not qualify. It serializes permission/audit writers, checks zero existing platform admins, original member identity and exact Agent/Revision, and executes the exact Revoke DELETE with a false predicate plus audit-INSERT validation in a rolled-back savepoint. PostgreSQL additionally refuses unproven custom triggers/RLS/inbound FK delete semantics. No Grant is made if executable Revoke/gate/identity is unproven.

Grant and grant audit are one transaction. Revoke only deletes the exact owned membership whose canonical identity matches this Run's grant audit; it never updates users.role, removes another user or deletes a foreign membership. Revoke is safe when repeated, after a failed transaction and after expiry. Any unknown extra admin remains untouched and final-zero verification fails closed. Role restoration is verified; original member role is never upgraded.

The Test request gate covers all `/api/v1/platform/` routes. It requires the existing signed session with current credential version, exact Principal/Tenant, exact Run header, active owned lease, unexpired scope, exact Agent/Revision/method/body and independent publication authorization. Skill management, other Agent/Revision/Tenant operations and forged/unsigned sessions reject. The existing formal catalog API still enforces real Runtime quality; this lease cannot make FAIL publishable. After revoke, both ordinary DB membership checks and scoped platform requests reject; an existing cookie retains only its original member identity.

Audit is linked to the full scope digest and previous canonical event hash. Operations have started/finished tickets and minimal response Task/Test IDs plus scoped effect hashes, never tokens, passwords, model text, config/secret content. Outstanding operations prevent ordinary Revoke. A crashed operation needs a fresh independent ROOT/06 quiescence attestation bound to exact Run, Source/tree, last audit, ticket and process IDs; a request boolean/lambda is rejected. Recovery closes the ticket and removes only the owned grant. Expiry denies new operations but does not disable cleanup.

Formal operator entry: `scripts/run_exact_test_admin_lifecycle.py` (`prepare|grant|revoke|recover|status`, exact `--run-id`). Requires Test, required-gate flag, `-B` and `PYTHONDONTWRITEBYTECODE=1`, ROOT approval/source match and established Test DB environment. It never reads project .env or Production fallback. Prepare alone reads an existing exact-Principal session from the fixed protected `/run/.../session.token`; no credential is generated, logged, committed or requested here. No CLI was run against live authority.

## Runtime/native compatibility code

`scripts/wechat_runtime_native_successor.py` checks admin state transitions and exact Runtime Test admission before delegating to the pinned current full persistent-config Guard. It preserves all other rows and original Config/Terminal/Receipt V2/seeding checks. Only proven scoped new rows/effects are projected into the pre-runtime structural view; whole-table exemptions and per-Task witness creation are absent.

Runtime Test Worker can race with API response: an authenticated, scope-bound START audit with the exact new Task/Test association authorizes A while still queued, not B/C. Normal startup still refuses a request in flight. Worker callbacks use the execution mode; publication still requires persisted PASS and independently authorized formal publish/enable transitions. Anchors must match existing historical pins; no published/current row is resealed as the original fixture. Unrelated Agent changes, persona edits, direct enabled edits, unauthorized admin membership, wrong associations, config/terminal mutations and tampered audit reject.

The native entry additionally pins current overlay `5ba582e...`, old imported d8a `a40e35dc...`, DCA `0e079b0f...`, current Policy/Approval/two witnesses, exact source files, real unchanged Schema/table set and old historical pin maps. It rejects Source symlinks, PYC/other extra files and missing/changed files. No old seal is edited or silently replaced. This whole native entry is not yet qualified by end-to-end isolated execution; that is the unique delivery blocker above.

## Evidence

Fresh deterministic suite-case executions:

| Suite | PASS | failed/errors/raw skips |
| --- | ---: | --- |
| New exact admin + Source-scoped API + native structural positive/negative/race tests | 28 | 0/0/0 |
| Inherited formal Runtime Test/quality/productization/snapshot/cache algorithm suite | 52 | 0/0/0 |
| Persistent Config component/historical graph regression | 42 | 0/0/0 |
| Secret backend/API/MCP gates | 60 | 0/0/0 |
| Personal Config | 23 | 0/0/0 |
| Actual accepted V2 Receipt/canonicalization/provision/seeding/cleanup fixtures | 95 | 0/0/0 |
| Inherited V1 Seeding strict guard | 30 | 0/0/0 |
| Total | 330 | 0/0/0 |

Positive Runtime Test PASS/FAIL is produced by actual signed-session API/AgentRuntimeTest/TaskService/AgentService/finished code with Runtime double, never a manually inserted PASS. It is NOT PRIMARY/real Provider quality evidence. DB/native ownership are isolated SQLite/explicit authority emulation; no PostgreSQL/systemd parity or complete native startup is claimed. The exact current DCA AST is hash-bound to 06 evidence. Tests preserve synthetic current config and terminal task bytes, delegate all other protected data to the structural graph, and revalidate retained tests after zero-admin revoke without DB mutation. A repeated pure read is not an actual service restart.

Initial fixture errors (TestClient exception setting, foreign-principal FK setup and missing Skill snapshot table) were corrected and full targeted suites rerun. No final failure/error/skip is hidden. There is no Full pytest, Stage 2, Candidate Build or WSL run.

## PYC status — no repeated PRIMARY handling

Read `PRIMARY_PYC_EXACT_CLEANUP_EXECUTION_V1.md` in full. 06 completed exact seven-cache cleanup at 2026-10-09 02:18 CST; subsequent full Source Guard PASS, zero admins, connected config and Secret v1 were reported. `PERSISTENT_INSTALLED_SOURCE_HASH` is CLEARED for that attested d8a predecessor. No cleanup, source-hash exemption, cache file deletion or server probe occurred here. The inherited local cache tests generate only temporary fixture bytecode; the new native entry requires strict maps plus `-B`/env=1 and adds no PYC exception policy.

## Required whole-entry validation and future 06 installation

Before READY, execute an isolated complete native-entry fixture with the new ROOT approvals, all exact imported module identities, source map, actual native snapshot/Schema representation, unchanged current Config/Terminal witness and fake encrypted-record metadata. Verify positive baseline/queued/running/PASS/FAIL/published/enabled/revoked states, all negative source/scope/schema/foreign rows, and actual recovery entry. Also prove native PostgreSQL Revoke semantics under the approved Target/role. Missing real-environment details remain `PENDING_06_READONLY_SNAPSHOT`; do not guess or relax them.

Only after qualification/review, 06 may obtain separate install authority for the new exact Application AND Tooling Source/tree, new Test successor/active/recovery roots and fresh scope. Old d8a Source/Guard/Receipt/Witness remain immutable. Do not reuse revoked run scopes or install this code under d8a's old source seal.

Future required configuration: Test only, exact PG Target, required admin gate flag=true, Redis queue, designated Test Tenant, native Runtime/Worker guard callbacks, root-pinned Source/Schema/policy/code/parent imports, original current witnesses and historical anchors, exact existing member credential session, explicit expiry/Run, independent publish authority. Seeding approval must be a new successor bound to the new source (old d8a exclusions cannot be silently repointed). Real Provider budget/network/model/credential authorization remains separate and default zero.

Safe recovery: stop admission, attest/quiesce exact in-flight API process/tickets where needed, invoke ONLY sealed new-source `recover`/Revoke, prove owned membership removed and active admins=0, retained tests/quality/data invariants still valid, then validate both active/recovery native contexts before any service restart. Never delete another principal or restore business rows with SQL. `-B`/env=1 apply to every probe/service. Revoke remains available after expiry.

Old d8a currently has a freshly attested safe baseline after PYC cleanup, but **once new Runtime Test/Publish/Enable/admin lifecycle data is persisted, direct rollback/restart with the old incompatible static guard is NOT SAFE**. Revoke alone does not erase retained runtime quality records or make old guard compatible. A separately reviewed compatible recovery successor is required; do not remove tests, change config/Secret or rewrite historical seals to force fallback.

`PRIMARY Changes=0` · `Production Changes=0` · `real admin creation/grant=0` · `Provider/WeChat/Image Calls=0` · `PRIMARY SSH attempts=0`.

STOP. No PRIMARY install, live Grant, real Runtime Test, Agent publish or CREATE_DRAFT call.
