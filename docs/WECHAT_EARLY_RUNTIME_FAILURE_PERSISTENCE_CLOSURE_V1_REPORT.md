# WeChat Early Runtime Failure Persistence Closure V1

Date: 2026-10-09 Asia/Shanghai.
Authorization: `ONE_FINAL_BOUNDED_RUNTIME_FAILURE_FIX_APPROVED`.

Offline qualification: **PASS** (387 inherited tests; 26 real PG16.6 scenarios,
including 21 negative assertions). Both previously blocked recovery scenarios
now PASS through PG restart; neither is counted as an expected failure.
Candidate publication still requires post-commit exact-Source replay and fresh
remote commit/tree verification; final delivery records those results separately.

## Identity and boundary

- Unchanged Application: `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`;
  tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5`.
- Direct Tooling parent: `adb99fbdfec50170de42cf118db6d5e588349770`;
  tree `a49a0415b48603b1034bc8ddc52b1bc26239a6e6`.
- Branch: `codex/wechat-early-runtime-failure-closure-v1`.
- Final commit/tree and fresh remote match are reported after commit; this file
  cannot contain its own commit hash. Application and Tooling identities are NOT
  interchangeable. This successor changes no Application files.
- The inherited adb tree already contains the earlier Test-only admin gate in
  `app/main.py` / `app/test_exact_admin_gate.py`. That prior change is unchanged;
  this report does not claim the entire Tooling tree equals the d8a tree.

PRIMARY_INSTALL_NOT_AUTHORIZED; REAL_PROVIDER_TEST_NOT_EXECUTED.
PRIMARY/Production changes = 0/0; Provider/WeChat/Image calls = 0/0/0.
No live Admin, Runtime Test, Publish, CREATE_DRAFT, installation or service restart.

## Root cause: B, legitimate pre-initialization failure

Audited actual d8a `app/service.py`, `app/runtime/codex_provider.py`,
`app/product_service.py`, `app/agent_runtime_test.py`, schema migrations and the
adb recovery report. All four Application files remain byte-identical in Git.

AgentService deliberately commits an auditable minimal Run before attempting
`create_session`, then attaches Task to its Run and intended Conversation UUID.
Conversation and its execution-context mapping are inserted in one transaction
only AFTER a real thread is returned. The model does not require a database FK
for this intended Conversation: no such FK exists in d8a schema.

Real CodexRuntimeManager records `runtime_start_requested`, then
`runtime_start_failed` on initialization failure. AgentService stores terminal
Run FAIL; TaskService stores Task FAIL with `runtime_start_error`; the formal
Runtime Test executor stores its real FAIL result. No thread/Conversation is
invented. Native previously demanded a Conversation for EVERY non-null Run ID,
before checking this legitimate terminal state.

The prior `CodexRuntimeProvider(None)` probe also caused a secondary exception
while collecting startup events, leaving Run running. That composition does not
match main.py. This round uses the real Manager and proves terminal failed Run
persistence; it does not disguise running as failed/completed.

## Minimal implementation

1. `enterprise_agent_poc/scripts/wechat_runtime_test_lifecycle_guard.py`:
   one bounded `pre_thread_failure` check in the existing formal-test validator.
2. `enterprise_agent_poc/scripts/exact_test_admin_lifecycle.py`:
   existing locked reservation read additionally includes `execution_events`,
   `task_events`, `generations`, required to reject contradictory effects.
   No new writer, SQL mutation, admission schema or recovery framework.
3. `enterprise_agent_poc/scripts/verify_wechat_recovery_admission_postgres.py`:
   extends the existing SHA-pinned 06 replay, no second recovery implementation.
4. This report.

The existing contract domains remain unchanged; exact successor Source/tree and
code/file pins version this implementation. Historical seals are not rewritten.

The missing-Conversation branch accepts ONLY:

- Exact formal Runtime Test/Task/Run/Tenant/actor/Agent/Revision/context/instance,
  fingerprint, model and v2 profile joins; existing Native admission still
  independently binds Environment, Source, original Scope and operation ticket.
- Test, Task, Run and trace payload all terminal failed; exact Task error and
  failure stage; database thread sentinel `pending`, payload thread NULL.
- Real timestamp order: test creation <= worker start <= Run creation <= Run
  completion <= Task completion, with timezone-aware timestamps and a UUID for
  the intended Conversation.
- Exactly the two Manager lifecycle events above, ONLY at `runtime_profile`
  or `provider_initialization`, before process/thread creation. Post-process,
  thread-start/resume, cancellation and unknown failures are NOT exempted.
- Persisted queued/worker_started/failed Task transitions, no delta/completion;
  no runtime/tool/final-response success, result, assistant message, debit,
  generation, Conversation owner/mapping/turn event or reused Task/Run relation.
- The unchanged formal executor result recomputation MUST still match exactly.
  The branch does not return early or grant PASS/publication eligibility.

Whenever a Conversation exists, its existing exact Tenant/Agent/profile/context
and owner checks remain. A normal running/completed Run with no Conversation is
still rejected. Source checks, historical Action classification, Native ledger,
Config/Terminal witnesses, Receipt V2/seeding and Productization quality rules
are unchanged. CREATE_DRAFT remains blocked.

## Verification and evidence limits

Inherited targeted offline regression: **387 tests, 0 failed/errors/skips**:
36 historical Actions + 21 PREPARE + 28 Admin/Native + 52 Runtime/quality +
42 Config + 60 Secret + 23 Personal Config + 95 Receipt V2 + 30 V1 Seeding.
Their existing SQLite/Runtime Double/authority emulation is explicitly NOT the
critical real-PG evidence below.

Real-PG suite: 26 independent PostgreSQL **16.6** scenarios, including the
original 23 and 3 added cases; the negative scenario contains 21 explicit
rejection assertions. Final exact-Source replay is required after commit.

| Coverage | Required result |
|---|---|
| Previously blocked `failed_runtime` | Actual failed reservation, child exit, protected Recover, durable Admission, Admin=0, unchanged Task/Test, Native adapter PASS before/after real PG restart |
| Previously blocked `early_failure` | Actual Manager initialization failure, failed Task/Run, absent Conversation, normal Revoke, adapter PASS before/after real PG restart |
| `profile_failure` | Real pre-process filesystem failure at runtime_profile, real executor FAIL persistence, no Runtime Double |
| `manager_startup` | Actual SDK/local App Server + Skill discovery; no model Turn; queued reservation remains non-quality; normal Revoke/restart PASS |
| `early_failure_negatives` | 21 negative assertions against snapshots generated by the real formal executor on PG; no fabricated positive PASS |
| Original remaining 21 | Queued/no reservation/enqueue failure, normal finish, exact identity/proof/lease rejection, interrupted recovery transaction rollback, repeat recovery, forged admission rejection |

The 21 negatives cover normal/completed Run without Conversation, wrong failure
code/Tenant/actor/Revision/trace context, missing or post-thread startup events,
fake runtime success, actual tool effects, missing timestamp/transition evidence,
persisted result/image, conflicting owner, cross-Tenant Conversation, missing
authorization ledger, failed/no-evidence publication, and disabled CREATE_DRAFT.

Known blocked cases are no longer expected-rejection tests. They MUST succeed
through real recovery/restart; otherwise this candidate is NO-GO.

Local evidence directory (outside Git):
`C:/Users/猪猪/Documents/ChatGPT/ky_web/.wechat-early-runtime-failure-v1/`.
`pg-draft-confirmed.json` is adb plus byte-pinned draft edits;
`pg-final.json` is the final exact committed Source replay. Earlier failed probe
logs are retained, not overwritten or counted as successful qualification.

This is explicitly authorized targeted recovery parity, NOT Full pytest,
Stage2, Candidate Build or PRIMARY qualification. Retained local WSL Linux
UID1000, Python 3.11.16 and PG16.6 are used. Each case creates a fresh mode-0700
marked cluster, LF Source clone and private user/network namespace; no existing
cluster, service, port or credential is reused. Startup-only SDK uses an invalid
synthetic fixture key and disabled loopback endpoint, no model request or egress.
Real reservation APIs, PG transactions, process death/waitpid and PG stop/start
are exercised. Temporary owned roots are precisely removed after shutdown.

Authority/root-proof inputs remain isolated emulation. Native `validate_records`,
`validate_ledger`, `validate_snapshot` run with a strict synthetic predecessor
callback, not the installed PRIMARY Root wrapper. The actual current overlay
SHA `5ba582e45b55e35aaeae83d5de995db00853ae83df45f0dab58d7d361c0c2c47`
was checked locally; its immutable identity and existing full predecessor call
remain unchanged. No claim of fresh PRIMARY Native status or live model quality.

## 06 acceptance and safe recovery

06 must independently approve/bind the NEW Tooling commit/tree, changed code
SHA and installed file map. Keep d8a Application provenance separate; preserve
existing immutable parent pins/receipt/config/terminal evidence and actual
Secret version1. Do not reuse adb/b34 approval as successor installation authority.
No new per-record witness, retrospective PASS or resealed business rows.

Before any separately authorized PRIMARY action: fresh read-only identity,
schema/table-set/source integrity and current Config/Secret checks; require
`APP_ENV=test`, exact registered test switch,
`PYTHONDONTWRITEBYTECODE=1`, `python -B`, protected scope/policy/approval and
independent dead-operation proof. The consistent snapshot must include the
three additional evidence tables. Keep API/worker quiescence explicit.

Actual existing operator entry (not executed here):

```sh
python -B scripts/run_exact_test_admin_lifecycle.py status --run-id EXACT_APPROVED_RUN_UUID
```

`recover` at that same entry WRITES the Test database and remains PROHIBITED
this round. Grant and Runtime Test likewise require separate live authority.
On isolated Linux, repeat all `CASES` of:

```sh
python -B scripts/verify_wechat_recovery_admission_postgres.py \
  --probe SHA_VERIFIED_06_PROBE_PATH --candidate EXACT_CLEAN_SUCCESSOR_CHECKOUT \
  --host-net ORIGINAL_NETWORK_NAMESPACE --case failed_runtime
```

Requires UID1000 in a new user/net namespace with its own loopback,
`GIT_COMMON_SOURCE`, existing registered PG16.6/venv paths and no project .env.
06 probe SHA is
`45b6b4762ce6796d552ee9b9feb4308fd044fcb433091eac1b5565020c138ebe`.
Do not use `--draft` for sealed verification. Inspect original Root trust entry
and full predecessor against real protected local evidence on PRIMARY only in
the next separately authorized read-only acceptance.

Safe recovery remains: exact dead-operation proof -> revalidate committed
reservation in the protected transaction -> Admission/abandonment/revoke atomic
commit -> Admin=0 -> read-only Native status -> PG restart on isolated fixture
-> Native status again. No restart/continuation of failed model execution.

Direct rollback to old d8a Guard on a database containing these formal Runtime
Test records remains blocked (`NO_RUNTIME_QUALITY_EVIDENCE`); adb's old lifecycle
reader also rejects pre-thread failures. Never delete Test/Task records or
invent Conversations to obtain startup PASS. Use only a separately qualified,
data-compatible successor/recovery path preserving business and audit records.

PRIMARY_INSTALL = NOT_AUTHORIZED; PRIMARY_LIVE_RECOVERY = NOT_EXECUTED.
NATIVE_PRIMARY_STATUS = NOT_REVERIFIED; REAL_PROVIDER_TEST = NOT_EXECUTED.
