# Workbench CREATE_DRAFT Formal Execution Successor V1

## Result and approval gate

`WORKBENCH_CREATE_DRAFT_EXECUTION_BLOCKED`

Sole FIRST_FAILURE_POINT: `CREATE_DRAFT_SCHEMA_APPROVAL_REQUIRED`.

This is a **partial, non-executing safety candidate**, not
`WORKBENCH_CREATE_DRAFT_EXECUTION_CANDIDATE_READY`, INSTALL_READY, a Native
qualification, or WeChat E2E. No Migration016 exists in the migration directory
and no new schema was executed. Await approval of the minimal additive durable
operation contract in `WECHAT_CREATE_DRAFT_SCHEMA016_PROPOSAL_V1.md` before
implementing or admitting an uploading Action Task.

## Frozen Source and isolation

- Direct parent Application: `57fc4ec543a27a8bd876d8e0dc0fccc2676e27dd`.
- Parent tree: `07108f98c6c7e9b137c376fa6a454cffbc342ca9`.
- New independent branch: `codex/wechat-create-draft-successor-v1`.
- Worktree: `C:/Users/猪猪/.codex/worktrees/wechat-create-draft-successor-v1/ky_web`.
- Final Source/tree are the commit containing this report (returned separately
  after commit/fresh fetch; not a self-referential hash inside the commit).
- No changes to 2c91 Tooling, 02 integration/backend worktree, 03 WX-05 worktree,
  other dirty files, old approvals, release contracts or historical commits.
- PRIMARY changes=0; Production changes=0; Provider/WeChat/Image calls=0.

## Source audit and exact dependencies

| Existing source | Finding and boundary |
| --- | --- |
| `app/skill_dispatch_config.py:from_settings` | TEST-only sealed dispatch/runtime Source checks. PREPARE enabled with `--check`; CREATE_DRAFT explicitly disabled with same offline adapter. Unchanged. |
| `app/skill_dispatch.py:execute` | Authenticated signed context, exact published Skill revision/package, action scope, per-invocation UUID work directory and receipt. A new invocation directory on retry cannot provide confirmation-wide durability. Unchanged. |
| `app/wechat_action_contract.py:_task/resolve/prepare_secret_injection` | Current DB/token binding, Skill permission, connected/secret/network gate. Old intent matching of natural language is not a user-confirmation receipt. Unchanged; not repurposed to grant upload. |
| `app/wechat_prepare_action.py:normalize` | Only HTML + verification hashes are emitted, not title/digest/cover/image bundle manifest. Existing PREPARE output remains unchanged. |
| `skill_sources/wechat-html-draft/1.0.0/scripts/wechat_draft.py:publish_to_draft/verify_draft` | Existing image mutations, durable-file pending marker, no repeated pending mutation, draft/add and draft/get comparison. File state belongs to one work directory, not an atomic DB Action operation. No Skill changes. |
| `app/agent_execution.py:resolve/check_context/authorize_tool` | Existing exact revision/instance and Runtime quality checks must be reused, not replaced with a client confirmation flag. No quality or authorization changes. |
| `app/product_store.py:create_task/set_task/complete_task_success/recoverable_tasks` | Random Task UUID; mutable result slot; generic restart requeue. New confirmation identity and external-intent durability are absent. Targeted regression reproduces result overwrite/requeue. |
| `app/product_service.py:TaskService` | Existing queue execution and persistence-only recovery, not an external Action state machine. No new runner/queue and no Task execution changes in this candidate. |
| `app/controlled_skill_action.py` and `app/service.py:_run_controlled_action` | Existing zero-Provider TEST ticket and shared Run persistence. Protected authority explicitly requires WeChat calls=0. Cannot reuse historical tickets or infer upload authority from PREPARE. |
| `app/agent_productization.py:publish/set_instance_status` | Real Runtime quality eligibility and publish/enable gates unchanged. |
| `app/task_queue.py` and `app/worker.py` | Redis at-least-once delivery/redelivery. Task identity alone is not durable external-side-effect idempotency. |
| `app/tenant_secret_reference.py` | Existing ephemeral SecretLease/current version/connected checks, no new Secret backend needed. No secret is read by the new preview endpoints. |
| `app/static/wechat-article-ui.js` | Existing WX-05 sandbox preview and pending adapter unchanged. No guessed upload URL, no simulated success receipt. |

## Implemented files and behavior

1. `app/wechat_prepare_reader.py`: server-selected, owner-only PREPARE preview.
   Joins current user/tenant, message/conversation owner, exact completed Task/Run,
   immutable context, current published revision and enabled instance, Skill
   revision/package identity. Requires a unique successful PREPARE execution audit
   and matching disk receipt. Multiple PREPARE results do not select "latest".
2. Reader restricts artifacts to known relative paths derived from the verified
   server receipt and context. Rejects wrong path/MIME/size/hash, oversized or
   missing files, symlink/junction ancestors, changed files, duplicate JSON keys,
   wrong audit/action/tenant/agent/revision. No client artifact ref or filesystem
   path is accepted, returned or logged.
3. Response includes HTML in JSON, identity/version hashes, PREVIEW_ONLY,
   `action_authorized=false`, `upload_bundle_verified=false`. This is **not** a
   claim that the historical article was previously sealed as a full upload
   bundle or that HTML matches the model's message text. The returned version
   hashes both message and PREPARE receipt; a future confirmation must freeze and
   compare the full bundle. Preview still requires the existing sandbox/CSP.
4. `app/wechat_draft_api.py`: authenticated, no-store JSON GET prepare and
   availability. Availability remains BACKEND_PENDING. POST validates same-origin
   Fetch Metadata + an explicit custom header, then returns 409 pending schema
   approval. No client body can create an Action or grant a capability. No Task,
   secret read, audit write or external request occurs.
5. `app/main.py`: includes this limited router using existing `current_user` and
   store. No server startup, migration, settings, Provider or Skill changes.
6. `tests/test_wechat_prepare_reader.py`: isolated schema001–015 SQLite component
   fixtures, exact relation and filesystem negative tests, authenticated API,
   CSRF, pending/no-write behavior, existing result-slot/requeue evidence.
7. This report and the Schema016 proposal are review material, not authority.

## Routes and 03 integration boundary

Base: `/api/v1/agents/{agent_id}/messages/{message_id}/wechat-draft`.

- `GET /prepare`: owner-only PREVIEW_ONLY JSON; no filesystem refs in response.
- `GET /availability`: BACKEND_PENDING, `can_create_draft=false`.
- `POST` base: unauthenticated 401 / CSRF 403 / unavailable owner artifact 404 /
  authenticated valid-owner request 409 `CREATE_DRAFT_SCHEMA_APPROVAL_REQUIRED`.
- No draftStatus/priorReceipt endpoint is claimed implemented: there is no
  durable Action record yet. Never synthesize a prior success from PREPARE.
- Existing WX-05 stays on its pending adapter. Do not wire this POST as a working
  uploader. Future adapter needs immutable expected version, durable operation
  status and UNKNOWN display; it must not automatically resubmit after failure.

These new public read routes do not inherit or grant Test-only admin permission.
The normal session function checks current user enabled state and credential
version. The reader additionally checks current DB ownership/status.

## Action contract, idempotency and UNKNOWN: not yet implemented

The proposal specifies a versioned, revocable exact Action capability and one
new operation table, short transactional Task creation/unique confirmation,
fresh signed Task scope, current execution rechecks, per-mutation durable intent,
CAS/lease serialization, readback and UNKNOWN fail-closed handling. This candidate
does **not** claim duplicate Action creation, Worker restart, concurrent uploads,
partial uploads, network ambiguity or remote draft/get reconciliation are solved.
They are the next implementation scope after approval, not passing tests here.

No new execution permission, approval issuer, egress, global enabled flag,
temporary admin, fake model PASS or second infrastructure service was added.

## Test evidence

Targeted Python regression: **269 passed / 0 failed / 0 errors / 0 skipped**
(48 new reader/API components + 221 existing checks); 3 dependency/test-helper
deprecation warnings. WX-05 Node tests: **7 passed / 0 failed / 0 skipped**.
Final reader byte-level hardening was separately re-tested after the combined run;
that repeated subset is not added to the unique test count.
All fixtures/mocked results are automation only, not real WeChat/model execution.

Executed Python selection:

```text
python -B -m pytest -q -p no:cacheprovider
  tests/test_wechat_prepare_reader.py tests/test_skill_dispatch.py
  tests/test_wechat_action_contract.py tests/test_wechat_secret_provisioning.py
  tests/test_wechat_personal_config.py tests/test_wechat_skill_integration.py
  tests/test_agent_execution.py tests/test_product_auth.py
```

WX-05: `node --test enterprise_agent_poc/tests/wechat_article_ui.test.cjs`.

Final tested candidate file SHA256:

```text
app/wechat_prepare_reader.py
cd7335a9f21af71a8f0d412472a1e398d5165f9aad28be01018066a285b76416
app/wechat_draft_api.py
8b60ecadd2dcf230e8d4afbc6507b662a23a1f90fb563c8b909b4d7fc5ec8e6f
tests/test_wechat_prepare_reader.py
c7b5e1e0161c14cf9c52bfb2a8c018a4e80bf83317d26f5cd8707e9b4658921a
```

The Windows attempt could not collect because the existing test-isolation helper
calls POSIX `os.getuid`. A first Linux collection lacked `css_inline`; neither
attempt is counted as a passing run. For the subsequent targeted Linux component
run, the exact base Git archive was exported (avoiding Windows newline-induced
Skill hash drift), changed files overlaid, and the existing complete pinned
WeChat requirements installed using `--require-hashes --no-deps --only-binary`
into a new temporary dependency target, not the retained/shared venv.

This is explicitly NOT Full pytest, Stage2, Candidate Build, Linux Native service
qualification or PG migration testing. No real authority host was contacted.

## Approval and 02 / 03 / 06 handoff

1. Total control: review/approve the single additive operation-table contract,
   immutable complete PREPARE manifest and post-write recovery/schema floor.
   No execution is possible before this gate; no Migration016 applied here.
2. 01 after approval: implement DB constraints/repository transaction, exact
   revocable capability successor and shared signed Task/Run/Dispatcher path;
   retain the old disabled registration. Implement and test durable per-step
   intent/UNKNOWN/reconciliation. Obtain protected runtime scope approval before
   admission; the present request does not issue any real authority.
3. 02: integrate only reviewed source after fresh identity review; run isolated PG
   concurrency, queue redelivery/restart, crash-before/after-intent and partial
   upload fault tests. Do not treat this partial candidate as execution-ready.
4. 03: retain WX-05 BACKEND_PENDING and sandbox. Once approved backend exists,
   bind exact article version and display durable prior status; CONFIRMED must
   come only from verified backend receipt, never model response text.
5. 06: exact Application/dispatch/runtime Source/Tree and code-pin successor,
   Schema016 approval/migration/recovery floor, full Native tests and separately
   authorized WeChat test budget are prerequisites. Do not install this partial
   candidate or alter 2c91 Trial Tooling to bypass missing capabilities.
6. Existing V1 PREPARE records without a complete upload manifest must remain
   preview-only or undergo a newly authorized PREPARE/snapshot confirmation;
   never fabricate missing historical content/cover/image evidence.

PRIMARY_INSTALL=NOT_EXECUTED; REAL_CREATE_DRAFT=NOT_EXECUTED;
REAL_PROVIDER_TEST=NOT_EXECUTED; AGENT_PUBLISH=NOT_EXECUTED.
