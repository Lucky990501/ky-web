# CREATE_DRAFT durable operation: Schema016 approval proposal

Status: **PROPOSED / NOT APPROVED / NOT EXECUTED**.
This document is not a migration; nothing has been added to `migrations/`.
Base Application: `57fc4ec543a27a8bd876d8e0dc0fccc2676e27dd`.
Base tree: `07108f98c6c7e9b137c376fa6a454cffbc342ca9`. Schema remains 001–015.

## Why a dedicated durable record is proposed

Existing Schema001 `tasks.id` is unique, but `ProductStore.create_task()` allocates
a fresh random UUID per call. There is no confirmation identity or Action type.
`task_results.task_id` is unique **per Task**, not per user confirmation. Its
entire `result_json` is replaced by both `set_task(response=...)` and
`complete_task_success()`. `recoverable_tasks()` resets queued/running tasks to
queued, and Redis `recover_processing()` redelivers outstanding reservations.
Skill Dispatcher creates a new invocation/work directory on every execution.
The Skill's `state.json` and `.lock` protect one work directory, not this
cross-process/cross-retry business operation.

The added regression demonstrates the actual old result overwrite and requeue.
It does not execute WeChat or declare recovery safe.

Rejected shortcut: deterministic Task IDs alone prevent duplicate Task inserts,
but do not preserve submission intent, UNKNOWN, image upload receipts or immutable
article/account binding from the existing result writers. It is technically
possible to redesign `task_results` into a mixed result/operation journal, with
new rules for every writer, completion, retry, cancellation and recovery reader.
That is not the unchanged result-slot contract, and would spread the new durable
security contract across generic persistence code. A single additive operation
table is the smaller, explicit, independently constrained change proposed here.
No claim is made that an additional table is mathematically the only solution.

## Smallest migration scope for approval

One new table: `wechat_draft_operations`. No new queue, runner, Secret backend,
Tenant, Agent, revision or auto-enable behavior. No alterations to historical rows.

Proposed columns:

| Columns | Contract |
| --- | --- |
| `id` | UUID text primary key; server allocated operation/receipt identity |
| `environment`, `tenant_id`, `user_id` | exact authenticated server scope; FKs to existing tenant/user |
| `confirmation_key` | server canonical SHA256, not a client idempotency string |
| `action_task_id` | UNIQUE NOT NULL FK to newly created `tasks.id` |
| `source_task_id`, `source_run_id`, `source_message_id` | exact FKs to original Task/Run/message; original Task unchanged |
| `agent_id`, `agent_revision_id`, `skill_revision_id` | immutable exact bindings to existing records |
| `article_version`, `prepare_manifest_sha256` | content + immutable complete PREPARE bundle identity |
| `account_identity` | logical tenant/environment/AppID digest (no secret) |
| `secret_version`, `capability_identity` | exact pinned version + approved/revocable Action authority identity |
| `binding_json` | canonical immutable safe snapshot, hashes and refs only; no secrets or client file paths |
| `state` | CHECK: QUEUED, UPLOADING, SUBMITTING, VERIFYING, UNKNOWN, CONFIRMED, FAILED, REVOKED |
| `revision` | positive CAS revision, for serialized claims/updates |
| `lease_id`, `lease_expires_at` | bounded Worker ownership; never authorization by themselves |
| `intent_json`, `upload_receipts_json`, `draft_media_id`, `verification_json` | durable side-effect facts, sanitized and schema-validated |
| `created_at`, `updated_at` | server timestamps |

UNIQUE `(environment, tenant_id, user_id, confirmation_key)`.
The confirmation key binds Tenant/User/Agent, source message, article version,
complete PREPARE manifest and **logical target account**. Secret rotation and
new capability versions must NOT create a new confirmation identity or authorize
another draft for an unresolved/confirmed operation. Those version changes
invalidate execution eligibility, not the historical operation lookup.

Required DB guards in the same migration:

- INSERT association trigger verifies user belongs to tenant; source Task/Run/
  conversation/message belong to that user, tenant and agent; action Task has the
  same tenant/user/agent but is different from the source Task; exact immutable
  context revision/Skill binding and PREPARE snapshot relationship.
- Immutable-binding UPDATE guard for identity columns and `binding_json`.
- Enforced legal state transitions; no UNKNOWN→QUEUED/UPLOADING/SUBMITTING;
  no CONFIRMED→execution state. CONFIRMED requires persisted media_id and verified
  exact-account/content readback evidence, never client claims.
- No delete-based recovery. Cross-tenant/incorrect-source writes fail closed.

This is the requested minimal migration **specification for approval**, not
ready-to-run SQL. SQL and PG fault-injection tests follow explicit approval;
they have not been applied even to an isolated DB in this candidate.

## Atomic create and execution contract (not implemented/qualified yet)

1. Current authenticated session and same-origin CSRF validation. A confirmation
   request carries only server-visible message/Agent and expected version.
2. Re-read ownership, published revision + real quality eligibility, enabled
   instance, bound Skill, account connected and current secret version. No model
   text parsing, no client tenant/credential/path, no reused Task token.
3. Validate full PREPARE manifest (title/digest/HTML/cover/local images and hashes).
   Existing V1 HTML-only receipt is preview evidence, NOT a complete upload grant.
4. A short existing repository transaction serializes on the existing owner or
   instance row, looks up the unique confirmation, returns prior operation if
   present, otherwise inserts operation + new Task + signed-context association.
   A uniqueness conflict returns the committed operation, not a second upload.
5. Commit before enqueue. Existing queue recovery uses the operation association;
   no new queue. Existing TaskService/AgentService/Dispatcher get a bounded Action
   route, not a second Runner or a model-driven CREATE_DRAFT invocation.
6. Worker rechecks server-issued capability, current Publish/Enable, Task/Run,
   Tenant/User, Skill revision, connected account and exact secret version. The
   MCP boundary checks the newly signed execution/task scope again.
7. Durable per-step intent commits **before** each image mutation and draft/add.
   Only then use a short SecretLease and revision-pinned Skill execution, with
   approved restricted egress. No provider, no send/publish/delete endpoint.
8. A crash/timeout/invalid response after intent is UNKNOWN. No automatic upload
   or draft/add retry, including after PG/Worker/service restart. Preserve partial
   upload receipts. A known media_id permits draft/get verification only; no
   known id or trustworthy evidence remains UNKNOWN/manual inspection.
9. Only matching draft/get account, title/digest/content/images/cover evidence
   permits CONFIRMED. Receipt is read from the operation record, never model text.

There is no claim of WeChat server-side exactly-once. A fail-safe UNKNOWN can
occur even if no remote mutation happened; avoiding duplicates takes precedence.

## Versioned capability proposal

`WECHAT_CREATE_DRAFT_ACTION_CAPABILITY_V2`, issued through the existing protected
Source-bound authority path, not request JSON. Bind environment, Application and
dispatch/runtime Source/tree + code pins, tenant, Agent/revision, Skill revision,
Action/scope, account/secret version, validity/revocation and restricted network
scope. Deny by default. Separate current authority from persistent historical
receipts. Historical Test-only PREPARE authority and successful model quality
tests do not grant CREATE_DRAFT. Existing V1 disabled registration stays intact.

This proposal does not issue any approval or enable egress. The protected
capability issuer/loader successor must be approved and reviewed together with
the persistence integration; no permissive fallback to the old Test ticket.

## Rollback/data compatibility

Before any Action exists: additive table may remain while old Application runs
only if its exact schema/release contract accepts 016. No implicit acceptance.

After any Action exists: disable/revoke new admissions, drain/hold Action workers,
preserve UNKNOWN/intents/receipts and reconcile known ids without re-submission.
Old 57fc workers must NOT consume these Action Tasks: their generic execution
and requeue logic lacks the new contract. Post-write rollback therefore requires
an identity-bound compatible successor, not blind old-Source restart. Do not drop
the table or delete Task/Run rows to make rollback pass. Schema migration is
expand-only; no automatic down migration. 06 must approve exact forward/recovery
and schema floor contracts before install. 2c91 Tooling is unchanged here.
