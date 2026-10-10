# CREATE_DRAFT Schema016 / durable Action candidate

Date: 2026-10-10. Authorization:
`WECHAT_CREATE_DRAFT_SCHEMA016_ISOLATED_IMPLEMENTATION_APPROVED`.

## Decision and exact boundary

**Candidate preserved; NOT formally READY / NOT installable.**

`FIRST_FAILURE_POINT = SCHEMA016_NATIVE_FORWARD_RECOVERY_CONTRACT_INCOMPATIBLE`

This is a **static contract incompatibility**, not a claimed PRIMARY execution
or fabricated Native error. The existing `2c91d3b7ed4f8658d41662b000f5b075d1d57368`
Tooling has:

- `scripts/wechat_runtime_native_successor.py`: its formal parent branch requires
  `policy['schema_fingerprint'] == old_base_policy['schema015_fingerprint']`, the
  exact original table set, and actual snapshot equality; failure code
  `NATIVE_SUCCESSOR_ACTUAL_SCHEMA`.
- `scripts/runtime_recovery_binding.py`: `APP_SOURCE` is exactly
  `28e061a114118a27609330125c756e193d3248a6`, tree
  `8ff5e4a8ed26c32ba040b4fa27cf1d380f3c7541`; its pair check fails with
  `RECOVERY_ATOMIC_APPLICATION_REQUIRED` for another Application.

Adding an Action table and Action-aware Worker cannot satisfy those unchanged
contracts. PG/Redis restart safety demonstrated below is **not** proof that the
existing protected Native forward-recovery entry accepts Schema016. No parent
policy, table exemption, historical seal or 2c91 file was changed. Per the task's
explicit stop condition, no further Guard/Recovery architecture was added.

PRIMARY/Production changes: **0**. Real Provider/WeChat/Image calls: **0/0/0**.
No real approval issued, no Agent Publish/Enable, no real Secret modified.
Current d8a→28e Trial remains on its approved old-Schema contract.

## Git identity

Branch: `codex/wechat-create-draft-successor-v1`.
Direct parent: `bfac937949ebd3eeb49bca50b4ee857c7d09b32d`.
Parent tree: `e0037e623d23a4aed47fbfe9710948cbbb35ffaf`.
Lineage: `57fc4ec543a27a8bd876d8e0dc0fccc2676e27dd → bfac → this commit`.
The final response records this report's containing commit/tree and fresh remote
verification (a commit cannot contain its own hash). No other worktree is staged.

## Implementation

Only Application feature worktree files are changed:

| File under `enterprise_agent_poc/` | Responsibility |
| --- | --- |
| `migrations/postgres/016_wechat_draft_operations.sql` | One additive PG table; FKs, ownership/context association trigger, immutable binding, unique confirmation, CAS revision, legal transitions, append-only evidence, delete rejection |
| `app/wechat_draft_operations.py` | Atomic Operation + new Task + new Run + existing immutable context association; owner row lock; leases, committed intent, receipts, UNKNOWN and readback-only recovery |
| `app/wechat_draft_capability.py` | Default-deny protected Test-only V2 authority loader; exact Source/tree/code pins, expiry/revocation, exact principal/revisions/account/secret version, bounded WeChat endpoint scope |
| `app/wechat_draft_execution.py` | Existing TaskService Action branch orchestration, current ownership/Publish/Enable/quality checks, fresh signed Task context, short SecretLease and safe terminal synchronization |
| `app/skill_dispatch.py` | Existing signed Task/revision/runtime/workspace checks; journal-gated Skill subprocess; every HTTP request reauthorized; every mutation waits for committed PG intent |
| `scripts/wechat_draft_journal_child.py` | Internal adapter in the existing revision-bound Python runtime; calls unchanged Skill `publish_to_draft`/`verify_draft`/image validation; no independent queue/service/model runner |
| `app/wechat_prepare_action.py` / `app/wechat_prepare_reader.py` | New complete immutable upload manifest, owner-only validated bundle reading; old HTML-only PREPARE stays PREVIEW_ONLY |
| `app/wechat_draft_api.py` / `app/main.py` | Authenticated availability, confirm, status/prior receipt and known-id reconcile; same-origin CSRF, server-only article/account scope; existing queue enqueue |
| `app/product_service.py` / `app/product_store.py` / `app/worker.py` | Action routing before model path; no generic Action requeue reset; existing Worker repairs commit-before-enqueue gap and retains held lease reservations |
| `app/static/wechat-article-ui.js` / `app/static/workbench.js` | Existing WX-05 server adapter; durable receipt/UNKNOWN states, owner status query, no automatic resubmission; existing sandbox preserved |
| `tests/test_wechat_draft_operations_postgres.py` / `tests/run_wechat_draft_pg16.py` | Marked socket-only PG16.6 migration/concurrency/fault/restart, real Redis redelivery, real Skill subprocess with mocked HTTP |
| `tests/test_wechat_draft_capability.py` / `tests/wechat_article_ui.test.cjs` | Authority negatives and WX-05 API/status/security component coverage |

No Skill ZIP/source, historical migration001–015, Secret encryption, 2c91 Tooling,
Prompt or model configuration was changed. The UI skill influenced only clear
async/unknown status and explicit recovery messaging, not the existing design.

## Schema016 contract and actual-schema differences

Exactly one new table: `wechat_draft_operations`. Actual migrations001–015 are
used, not invented composite columns. Adds `action_run_id` and `context_id` to
the proposal, since `run_traces.run_id` and `agent_execution_contexts.id` are the
real identities. Existing `tasks.run_id` alone has no complete relational guard.

The insertion trigger verifies source Task/Run/message/result and owner,
conversation, action Task/Run and immutable context, exact Agent/revision and
Skill binding. All Operation identity fields and `binding_json` are immutable.
Unique `(environment, tenant_id, user_id, confirmation_key)` and unique new
Task/Run prevent concurrent duplicate creation. Original PREPARE Task stays
completed. Task/Run/Operation initialization shares a short PG transaction; no
Provider or network wait occurs inside that creation transaction.

Confirmation identity includes article/manifest and logical account, **not**
secret/capability version. Rotation cannot mint another upload for the same
confirmation. No migration rewrites or deletes historical rows. Empty-table
reapplication and the formal migration runner's checksum history are tested.

## PREPARE and execution authority

New PREPARE, when a server-owned versioned account exists, pins title, digest,
prepared HTML, cover, all local image bytes/lengths/hashes, image list, content
version, Skill revision/checksum, source Task/Run/user and logical account. Its
manifest hash is part of the disk receipt and existing `skill.execution` audit.
Reader rechecks those two receipts match, exact ownership and current published
revision/binding, safe regular paths (no symlink/junction/traversal), type/size and
bytes. Article version also binds the persisted message and receipt identity.
Remote/unpinned images cannot become upload permission. Historical HTML is not
retroactively assigned a manifest.

`WECHAT_CREATE_DRAFT_ACTION_CAPABILITY_V2` is loaded from the fixed protected
Test control path `/etc/enterprise-agent-integrated-test-v3.3/wechat-create-draft.v2.json`.
It requires root-owned immutable regular file and protected ancestors, exact
Application Git identity, complete code pins, exact tenant/user/Agent/revisions,
logical account and Secret version, non-revoked bounded validity (at most 24h),
and at most 32 requests per child invocation to only the five existing WeChat endpoints. Production
is explicitly rejected in this candidate. No approval file or issuance is
included; missing authority/network permission stays BACKEND_PENDING.

Existing V1 `CREATE_DRAFT enabled=False` is unchanged. A model/MCP call cannot
activate V2 by changing that flag or supplying HTML/paths. V2 consumes only a
durable server-created Action, issues a new task-scoped signature, reuses
Dispatcher authorization/runtime/registry checks and rechecks current capability,
article, connected Secret version, Publish/Enable and quality before execution
and each HTTP permit. Historical Test-only PREPARE quality cannot qualify it.

## Intent, ambiguity and recovery

Each upload and draft/add obtains a PG-committed unique intent before network.
Worker lease + row locking/CAS serialize effects. Receipts retain partial
uploads; immutable intent entries are never removed to make a retry legal.
Timeout, invalid response or interrupted ownership after any intent becomes
UNKNOWN. UNKNOWN cannot return to uploading/submitting/queued. No exactly-once
claim is made about WeChat's server.

Only an already-persisted valid media_id can enter explicit readback recovery.
That child is readback-only and rejects every mutation. It reconstructs payload
from the immutable bundle and acknowledged upload identifiers, then uses the
unchanged Skill's `draft/get` verification. Only matching readback commits
CONFIRMED; local Task completion or model prose cannot do so. Without media_id,
UNKNOWN stays manual-review-only; no client-supplied media_id is accepted.

PG commit before enqueue is recoverable by repeated confirmation or existing
Worker startup scanning pending Action records into the same Redis queue.
Busy leases raise the existing `TaskExecutionNotAuthorized` hold signal, avoiding
an ACK that would lose recovery work. Expired leases with intents become UNKNOWN.
No new queue, retry framework, Witness or permission system was introduced.

## API / WX-05

Base: `/api/v1/agents/{agent_id}/messages/{message_id}/wechat-draft`.

- GET `/prepare`: owner preview; no paths/Secret in response.
- GET `/availability`: READY only after server checks; otherwise BACKEND_PENDING;
  prior receipt takes precedence over current execution permission.
- POST base: only `{article_version}`; authenticated same-origin session,
  `Sec-Fetch-Site: same-origin`, `X-Workbench-Action: CREATE_DRAFT`. Creates or
  returns the same durable Action, then queues after commit.
- GET `/status`: owned durable operation/prior receipt, not model-generated data.
- POST `/reconcile`: same CSRF/owner checks, known durable media_id only, current
  capability required. Cannot accept a guessed id or resubmit draft/add.

Front-end status query is read-only. UNKNOWN and other existing operation states
disable new upload. Failure never triggers automatic POST. Legacy PREVIEW_ONLY
and missing capability remain closed. WX-05 sandbox CSP and no-script/no-network
preview are unchanged. No claim that the undeployed 71 business E2Es passed.

## Isolated automated evidence

Final targeted runs (not Full pytest / Stage2 / Native release qualification):

| Suite | Result |
| --- | --- |
| New PG operations + V2 capability tests | **67 passed / 0 failed / 0 errors / 0 skipped** (43 PG tests including 4 actual Skill subprocess/mock-HTTP cases; 24 capability components) |
| Existing reader, Skill dispatch, WeChat action contract, secret provisioning, personal config, Skill integration | **211 passed / 0 failed / 0 errors / 0 skipped** |
| Existing image provider, activity-plan runtime, Runtime Test lifecycle | **56 passed / 0 failed / 0 errors / 0 skipped** |
| WX-05 Node components | **9 passed / 0 failed / 0 skipped** |

Total: **334 Python + 9 Node**, without double-counting reruns. Existing
Starlette/unittest deprecation warnings are reported by the runner (3 in the
largest inherited selection); they are not skips or real model execution.

Runtime: Linux WSL (explicit user-authorized isolated test, not PRIMARY authority),
PostgreSQL **16.6**, Unix socket only, fresh uniquely marked cluster; real Redis
7.4.2 binary in a distinct temporary directory with TCP disabled. Databases use
synthetic tenants/users/articles only. Isolated DBs are removed by fixture teardown,
PG/Redis stopped, cluster/log evidence retained. No historical local data cleaned.

Complete 67-test PG/capability evidence directory:
`/private/tmp/ky-web-stage1-postgres.wechat016-ct3il9dt`.
Consolidated 334-test run directory:
`/private/tmp/ky-web-stage1-postgres.wechat016-kpqyxm7c`.
JUnit evidence: `/var/tmp/wechat016.ACbd9W/targeted-results.xml`.
Canonical test snapshot: `/var/tmp/wechat016.ACbd9W/enterprise_agent_poc`.
Exact launcher: `python -B tests/run_wechat_draft_pg16.py tests/test_wechat_draft_capability.py --tb=short`.
Use a separately installed, locked Skill dependency set in the isolated test
PYTHONPATH; do not copy test doubles or mock HTTP into an installed runtime.

Covered: migration and old row equality; concurrent unique confirmations; failed
insert rolls Task/Run back; cross-owner/revision/source rejects; immutable columns;
intent commit observed from a second connection; expired/held lease; pre-intent
crash; response-lost UNKNOWN; no-id rejection; known-id readback; PG restart;
Redis processing redelivery and commit/enqueue gap; Secret rotate/revoke; article
change; no quality/unpublished/disabled rejection; auth/CSRF/extra-field rejection;
real Skill child mocked success/cover-loss/draft-loss/readback-mismatch+recovery.

Mock authority/quality/installed-runtime boundaries are explicitly marked in the
fixture. No test inserts a fake **real model PASS** or calls a real Provider.
HTTP responses in subprocess tests are in-memory synthetic mocks. These results
do not establish real account, egress, installed runtime seal, PRIMARY service
startup or live WeChat draft creation qualification.

## 02 / 03 / 06 next gate (requires separate authority)

1. **02** review Schema floor016, exact Application/Tooling pair, migration
   admission and an Action-aware protected forward-recovery successor. Preserve
   immutable old015 contract; do not re-seal current data as historical pins.
2. **06** must not use unchanged 2c91/28e recovery for post-016 Action writes.
   Review the new table/state machine, durable intent, held lease and UNKNOWN
   retention through the actual Native collector/startup/recovery path. Revoke
   admissions before recovery, retain all Action/Task/Run/receipt data, and use
   only a separately approved compatible Worker. Never down-migrate/delete the
   table or blindly start d8a/28e/57fc Workers over Action data.
3. **02/06** separately approve an immutable V2 capability and new exact dispatch
   seal after reviewing this Source/tree/code pin set; validate existing
   revision-bound Skill runtime installation. No real issuer authority or
   Production permission is granted by this candidate or its tests.
4. **03** run actual WX-05 deployed UI/status/accessibility and the outstanding
   business E2Es after approved integration. Old PREPARE remains preview-only;
   regenerate complete PREPARE under the new source before requesting upload.
5. Real connected account/egress/WeChat draft+readback acceptance needs separate
   bounded customer/test authorization and budget. It was not executed here.

`PRIMARY_INSTALL = NOT_AUTHORIZED` · `PRODUCTION = UNCHANGED`
`REAL_CREATE_DRAFT = NOT_EXECUTED` · `RELEASE_READY = NOT_GRANTED`
