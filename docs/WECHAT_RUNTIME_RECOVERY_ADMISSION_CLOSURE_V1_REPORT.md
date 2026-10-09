# WeChat Runtime Recovery Admission Closure V1

Date: 2026-10-09 Asia/Shanghai
Authorization: EXISTING_RECOVER_ADMISSION_MINIMAL_FIX_APPROVED

## Decision

**WECHAT_RECOVERY_ADMISSION_FIX_NO_GO**

Unique FIRST_FAILURE_POINT: `RUNTIME_LIFECYCLE_RELATION:conversations`

The interrupted queued/valid failed-enqueue reservation admission defect is
fixed. Full Safe Recovery is **NOT READY**: a real formal Runtime startup failure
before thread creation still has no Conversation, and the unchanged lifecycle
validator rejects it. This is also reproduced inside Recover, which correctly
rolls back rather than inventing a Conversation or admitting incomplete evidence.

This commit is a NON-INSTALLABLE partial fix / diagnostic candidate. Publication
does not grant installation, live recovery, Runtime Test, Provider or Publish
authority. No new recovery/permission framework or single-record Witness.

PRIMARY_INSTALL = NOT_AUTHORIZED
PRIMARY_LIVE_RECOVERY = NOT_EXECUTED
REAL_PROVIDER_TEST = NOT_EXECUTED
PRIMARY changes = 0; Production changes = 0; Provider/WeChat/Image calls = 0/0/0.

## Frozen input and real failure

- Application: `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7` (unchanged).
- Direct base: `b34e62cfb1a8567db89f823e9191fad360fb4088`.
- Base tree: `dcecf7e53023e054707e0e7142325025b129a206`.
- 06 report: `WECHAT_B34_SAFE_RECOVERY_QUALIFICATION_V1_REPORT.md`.
  SHA256 `b9b61bce437de5a109608c9773d3ded32e7e54976dd2c9d89bcddf133c5bb318`.
- Original 06 real-PG probe: `.wechat-b34-recovery-readiness-v1/isolated_pg_recovery_probe.py`.
  SHA256 `45b6b4762ce6796d552ee9b9feb4308fd044fcb433091eac1b5565020c138ebe`.
- Original interrupted test/task:
  `605be2e4-80be-401a-a8c2-4da06be7e6ce` /
  `b2cdd441-3d9a-4fdd-8df7-17e0b2ca8351`; Task run_id legitimately NULL while queued.

Real defect: only `operation_finished` stored durable admission. The existing
Recover checked dead-process proof, wrote ticket-only `operation_abandoned`,
deleted its owned Admin membership, and wrote `revoked`. The reservation remained
but the Native ledger's in-flight inference disappeared with the abandoned ticket.
Changing a Guard error or recovery flag would not repair this missing fact.

## Minimal transaction fix

Changed executable logic only:

1. `enterprise_agent_poc/scripts/exact_test_admin_lifecycle.py`
2. `enterprise_agent_poc/scripts/wechat_runtime_native_successor.py`

Additional test/report files:

3. `enterprise_agent_poc/scripts/verify_wechat_recovery_admission_postgres.py`
4. This report.

Existing protected `recover` → `ExactTestAdmin.revoke(load_dead_operation_proof)`
now identifies exactly one outstanding original operation. For Runtime Test it
revalidates the original subject/binding, locks the relevant persisted tables in
the existing PG transaction, and checks:

- Original scoped authorization run_id (distinct from nullable Task run_id),
  Environment, Application/Tooling Source/tree and scope SHA through the existing
  authority/history/proof loaders. No request-supplied Synthetic declaration.
- Original actor, Tenant, Agent, Revision, fingerprint, route/body, ticket,
  pre_test_ids, original operation time and single-host Test DB identity.
- pre_test_ids still exist on the exact Revision; no filtering away foreign new
  records to make an ambiguous reservation look unique.
- At most one new runtime reservation. Its creation must be at/after operation
  start, inside the original lease and not in the future.
- Existing formal lifecycle validation of actual test/task/context/instance/
  Task Run, state and executor result. `failed` alone is never proof.

The new abandonment payload contains `recovered_admission` (empty when no
reservation committed): runtime_test_id, task_id, nullable task_run_id, context_id,
canonical creation time, original operation_ticket and pre_test_ids digest.
The unchanged common audit envelope binds actor/Tenant/Agent/Revision/authorization
run/source/scope and the original audit hash chain.

Admission fact, abandonment, owned membership deletion and `revoked` all commit
or roll back together. Normal `finish_operation`/Revoke remain unchanged. Native
replay independently computes and compares the same exact association before
retaining admission. Abandonment is NOT appended as a successful Publish/Enable
operation and does not mint a PASS, HTTP success or execution permission.

Old evidence/receipts/seals are immutable. Old ticket-only abandonment rows are
not silently repaired: a missing admission still fails the existing equality
check. `EXACT_TEST_ADMIN_GRANT_REVOKE_V1` and
`WECHAT_RUNTIME_TEST_NATIVE_SUCCESSOR_V1` remain their existing contract domains;
the successor Source and byte pins version the implementation. Old readers do
not support the new fact and must not be used on new lifecycle data.

An expired lease cannot authorize a new operation or a reservation written
outside the lease. Existing cleanup of an otherwise valid expired lease remains
available; expiration is not a reason to leave a permanent Admin behind.

## Real PostgreSQL evidence and limits

Targeted recovery/parity investigation only; **not Full pytest, Stage2 or Candidate
Build**. No PRIMARY SSH, DB connection or service action.

- Local retained Ubuntu-22.04, Unix UID1000.
- PostgreSQL **16.6** verified with the actual binary's `--version`.
- Existing binary: `/home/lucky/.cache/enterprise-agent-test-runtime/postgresql-16.6/bin`.
- Interpreter: `/home/lucky/ky-web-release-test/enterprise_agent_poc/.venv/bin/python`,
  Python 3.11.16. This is local evidence, not default PRIMARY release authority.
- Every case: new private user/network namespace, loopback only, new mode-0700
  marked PGDATA/socket/log/object/runtime directories and LF Git clone from exact
  blobs. No existing cluster, Source checkout, data directory or port altered.
- Real formal API/AgentRuntimeTest.run(), actual PG migrations 001–015,
  actual child exit/waitpid, real PG transactions and stop/start.
- Neither SQLite nor a Runtime Double replaces critical recovery tests.
- Existing 06 authority loader/capability/root-proof INPUT emulation remains
  explicitly isolated. Positive dead-process cases observe actual API child
  death. This is not proof of PRIMARY Root file ownership/approval trust.
- Native `validate_ledger`, `validate_records` and `validate_snapshot` execute.
  The predecessor callback pins synthetic protected baseline rows and exact
  task ownership, but is an explicitly strict SYNTHETIC predecessor emulator,
  not the installed full PRIMARY Root wrapper.
- Negative DB mutations are disposable tamper fixtures, never positive setup or
  manual insertion of PASS/Test rows. Rejection preserves the complete snapshot.
  Rejected cases retain owned Admin for fail-closed inspection until teardown of
  the whole disposable fixture; teardown is not counted as a successful Revoke.
- All owned temporary clusters are stopped and precisely removed; old evidence,
  caches, checkouts and user dirty/untracked files are preserved.

### Targeted 23 cases

23 assertion cases passed, 0 assertion failures/errors/skips. **This is not 23
successful Recoveries**: two cases intentionally reproduce the outstanding
functional readiness block.

| Group | Cases | Actual result |
|---|---|---|
| Valid persisted recovery | no_reservation, queued, failed_enqueue | Admission fact correct (or empty), Admin=0, no PASS, Task/Test unchanged, repeat recovery idempotent, guard before/after PG restart PASS |
| Normal lifecycle | normal_finish | Formal middleware finish admission retained, Revoke and post-revoke permission rejection, PG restart PASS |
| Double crash | crash_after_admission, crash_after_delete | Actual recovery subprocess exits inside transaction; admission/DELETE/audit all rollback; second protected Recover succeeds; Admin=0; PG restart PASS |
| Exact association integrity | forged_admission | Recomputed negative copy hash chain cannot hide mismatched task association; genuine DB unchanged; guard/restart PASS |
| Unsafe identity/proof/state | ambiguous, wrong_tenant, wrong_agent, wrong_revision, wrong_actor, wrong_scope, wrong_source, no_proof, stale_proof, reservation_outside_lease, start_outside_lease, wrong_ticket, wrong_pre_test_ids | REJECT; complete database hashes unchanged |
| Expiration | expired_new_operation | New execution REJECT; existing cleanup restores Admin=0; guard/restart PASS |
| Known block in Recover | failed_runtime | Actual failed Task/Run with no Conversation; Recover REJECT/rollback; **NOT a recovery PASS** |
| Known block after normal Revoke | early_failure | Real Manager startup fails locally; Task=failed, Run=failed, Conversation absent; Guard REJECT both before/after PG restart |

Local detailed draft evidence (not committed because run-dependent):
`C:/Users/猪猪/Documents/ChatGPT/ky_web/.wechat-recovery-admission-closure-v1/pg-regression-draft.json`.
Post-commit exact-Source replay is saved separately as `pg-regression-final.json`.
Draft evidence explicitly means b34 plus byte-pinned tooling edits, not an
approved installation identity. Final commit/tree and fresh remote result are
reported outside this file to avoid self-referential Commit identities.

### Inherited targeted offline regression

387 case executions / 0 failed / 0 errors / 0 skipped:

- all historical Actions: 36; previous exact PREPARE: 21;
- Admin/Native lifecycle: 28; Runtime lifecycle/quality: 52;
- Persistent Config: 42; Secret backend/API/MCP gate: 60;
- Personal Config API: 23; Receipt V2/Seeding: 95; V1 Seeding: 30.

These existing Windows regressions explicitly use isolated SQLite/Runtime
Doubles/native authority emulation. They are not substituted for the 23 PG
scenarios, fresh PRIMARY validation or real model quality evidence.
Config/Terminal/Receipt/Seeding old implementations and snapshots remain pinned;
current live config acceptance is inherited only from 06's b34 report, not a new
PRIMARY observation this round.

## Additional Runtime diagnosis — STOP boundary

The 06 `CodexRuntimeProvider(None)` harness is not main.py's Manager construction.
`create_session()` accesses `.get` on None. AgentService's exception handler then
calls `startup_events()`, which accesses that same None; it throws before
`finish_run_trace()`. This accounts for the harness's `Run=running`.

We replayed with the actual `CodexRuntimeManager`, `SkillDeployment` and
`RuntimeTokenIssuer`, not a Double. The manager reads only a deliberately absent
fixture-only API-key environment name and fails before starting a Provider
process/turn. Formal TaskService/AgentRuntimeTest persist actual FAIL evidence.

Diagnostic IDs from the independent original-b34 reproduction:

- Test: `a44be464-3813-4633-9eeb-b4a4c3bbc339`.
- Task: `dffaf498-60a6-466d-a561-dc8e0e9701d6`.
- Run: `f439f7e6-382e-48e7-aaff-ba175fa451f6`.
- Intended Conversation: `430260a3-2680-42cc-bbfe-94198e898f2f`, genuinely absent.
- Task=failed, Run=failed, Admin after normal Revoke=0.
- PG restart preserves all-row hash
  `c6083b682fea0d224ce62aba1fe94f543afc61e7ec282d83e6be26af4bfc2afd`.
- Guard error before and after restart:
  `RUNTIME_LIFECYCLE_RELATION:conversations`.

Application AgentService deliberately persists a minimal Run before starting
the runtime (app/service.py create_run_trace), and inserts Conversation only
after create_session succeeds. Its valid startup exception path finishes the
Run as failed. The Native lifecycle validator unconditionally requires a
Conversation whenever run_id exists, before distinguishing startup failure.
Thus the remaining conflict is Native lifecycle compatibility, NOT evidence
that a normally constructed Application leaves this Run running.

**Proposal only, not implemented:** a separately approved narrowly defined
pre-thread FAILED state could verify the exact Task/Run/context, failed startup
trace/lifecycle, absence of successful result/message/charge/artifact and absence
of thread creation, while retaining strict Conversation ownership whenever one
exists and for successful Runs. It must confer zero quality/publish permission.
Do not invent a Conversation, mark a Run completed, or disable ownership checks.
No Application change is presently justified by this reproduction. No widening
of this candidate's scope is performed; the unmet condition keeps NO-GO.

## 06 entry and safety handoff (NOT authorized to run on PRIMARY)

Actual operator entry is `scripts/run_exact_test_admin_lifecycle.py`, not a
nonexistent CLI in the Native adapter module. Existing environment must contain
APP_ENV=test, ENTERPRISE_POC_EXACT_TEST_ADMIN_REQUIRED=true,
PYTHONDONTWRITEBYTECODE=1 and the approved protected Test database configuration.
Run as registered Linux UID1000 with `python -B`.

Read-only status, only after separately approved exact successor registration:

```sh
python -B scripts/run_exact_test_admin_lifecycle.py status --run-id EXACT_APPROVED_RUN_UUID
```

**WRITES Test DB; PROHIBITED this round / NOT installation-qualified:**

```sh
python -B scripts/run_exact_test_admin_lifecycle.py recover --run-id EXACT_APPROVED_RUN_UUID
```

Scope/approval/native-policy are loaded exclusively from existing protected
`/etc/enterprise-agent-test-exact-admin-v1` files. The successor must separately
bind its actual new Application/Tooling Source/tree, exact principal/Tenant/
Agent/Revision/run, scope SHA, changed writer/reader code bytes and installed file
map. Preserve all immutable predecessor pins/Config/Terminal witnesses/Receipt
V2/seeding identities; never reseal mutable current rows as an old baseline.
Do not reuse b34 approvals as authorization for this successor. No root files or
scope versions were changed here.

After quiescing and proving the exact API process, a separately approved fresh
protected dead-operation proof binds Source/tree/run, original outstanding
ticket(s), actual process IDs, last audit hash and observation time. The actual
Root trust entry must be tested by 06; local injected proof is not a substitute.

Qualification replay entry (separate local namespace / disposable cluster only):

```sh
python -B scripts/verify_wechat_recovery_admission_postgres.py \
  --probe SHA_VERIFIED_06_PROBE_PATH --candidate EXACT_CLEAN_SUCCESSOR_CHECKOUT \
  --host-net ORIGINAL_HOST_NET_NAMESPACE --case queued
```

The replay runner requires `GIT_COMMON_SOURCE` pointing to the registered local
Git repository, a private user+net namespace (keep-caps solely to raise that
namespace's loopback), UID1000, retained PG16.6/venv paths, and no project .env.
Enumerate all `CASES`; no --draft for final exact-Source tests. Windows worktree
metadata may require read-only GIT_DIR/GIT_WORK_TREE mappings; clear those for
the disposable LF clone. No prior clone/PGDATA is reused or copied to PRIMARY.

**Recovery/rollback constraints:** old d8a/current old Guard cannot safely accept
new formal Runtime Test records (06 verified NO_RUNTIME_QUALITY_EVIDENCE). Direct
rollback of only the code/Guard is blocked on such data. Preserve durable records,
Config/Secret version1 and old evidence; never delete Task/Test or alter quality
to fake startup. A data-compatible separately qualified successor plus root
proof, Native status and post-PG-restart checks is required before future recovery.
This candidate still lacks that full qualification because of the Conversation
gap. PRIMARY remains unchanged with its existing baseline guard/services.

All future Python probes retain `PYTHONDONTWRITEBYTECODE=1` / `python -B`. The
previous exact PYC cleanup is not repeated; no file-map or sealed manifest bypass.

No install, live Admin Grant, live Runtime Test, Publish/Enable or CREATE_DRAFT
operation follows this report. CREATE_DRAFT remains disabled. STOP.
