# Required tool dependency retry / supersession V1

Scope: backend architecture, tests and isolated replay only. No Production
mutation, original Task replay, Provider call, frontend, Skill or Prompt change.

## Audit of existing semantics

- Creation: `CodexRuntimeProvider._runtime_turn_from_result` stored
  `dependency_id = SHA256(full tool arguments)`. It was a request fingerprint,
  not a semantic requirement or an individual attempt ID. Equal arguments
  shared a bucket; changed input created a new bucket.
- Attempts: SDK MCP thread items were retained as `mcp_calls`, but lacked an
  explicit durable attempt identity, parent or supersession state.
- State: `AgentService._completed_trace` grouped `(server, tool, argument hash)`
  and required the last attempt in **every** group to succeed. There was no
  independent logical dependency state machine.
- Grounding had a separate implementation of essentially the same validator.
- Run finalization checked runtime terminal proof and required-tool completion
  before allowing product result persistence. The failure was therefore a real
  canonical Task failure, not an SSE transport/UI false failure.

The 2026-09-29 diagnosed case rejected `3:4` before the image Provider call,
then succeeded with `4:5`. Different argument hashes left the rejected request
as a permanently unsatisfied requirement. Original database/objects are NOT
reinterpreted or repaired by this implementation.

## Contract

`app/tool_dependencies.py` is the shared runtime validator. The new source of
truth is `logical_tool_dependencies` in existing Run Trace JSON; there is no
migration. The historical `mcp_calls.dependency_id` argument hash remains for
compatibility and is **not** the new logical identity.

A logical dependency records `dependency_id`, tool/server/execution scope,
requirement identity (initial attempt + full request fingerprint), ordered
attempt IDs, active attempt, status and satisfied flag. A new SDK tool call
starts an independent requirement slot, even for identical arguments. A
validated explicit retry inherits its parent's slot. Older observations without
execution scope retain the existing exact-request last-attempt fallback; they
can never use changed-input supersession.

Attempts retain SDK tool_call_id, scoped attempt_id, argument/field fingerprints,
result status, failure category, observed start/completion timestamps,
retry_parent and superseded_by. Rejected attempts stay failed; supersession is
a separate audit disposition. No raw arguments or enterprise material is added
to the new audit fields. Non-streaming adapters timestamp collection rather
than pretending to know an unobserved actual start time.

### Explicit lineage, not an automatic semantic guess

1. A platform tool raises `ToolInputValidationError` **before Provider calls or
   business side effects**, naming the fields which may be repaired.
2. The MCP adapter returns an error receipt with a random `retry_of`, full
   request fingerprint, rejected-field allowlist, failure category and proof
   that Provider was not invoked. The response instructs the caller to copy
   `retry_of` for this same requirement and omit it for an independent one.
3. The model/tool caller explicitly supplies that receipt on the corrected
   call. Existing Prompt/Skill content is not changed.
4. Runtime accepts the lineage only for a unique earlier failed receipt in
   the same SDK turn, server and tool, with exactly the same argument keys and
   unchanged hashes for every field outside the repair allowlist.
5. Successful active attempts satisfy their logical requirement. All active
   logical requirements (plus existing configured required-tool checks) must
   pass. Success of one tool name cannot satisfy another independent slot.

No token, unknown/forward/reused/ambiguous token, changed prompt/references,
cross-turn/server/tool, malformed receipt, Provider side effect, or duplicate
attempt identity fails closed. A caller which corrects input but does not copy
`retry_of` still fails closed. This V1 intentionally does not auto-merge the
historical unlinked `3:4 -> 4:5` pair based only on adjacent calls/prose.

### Failure categories

| Category | Changed-input supersession V1 |
| --- | --- |
| argument_validation_error | Yes, typed pre-execution receipt + explicit lineage |
| recoverable_tool_input_error | Validator supports the same receipt constraints; no new emitting tool in V1 |
| provider_timeout | No |
| provider_5xx | No |
| provider_policy_rejection | No |
| permanent_dependency_error / unknown | No |

The image adapter is the first opt-in tool. Generic validator fixtures exercise
knowledge, asset, document-export and custom Skill-tool observations. Those
existing tool schemas are not expanded or given inferred retry permission.
Existing bounded knowledge transport retries inside its service are unchanged.

## Artifact, persistence and accounting

The image MCP return annotation is `dict[str, Any]`, enabling FastMCP structured
output. The previous bare `dict` produced text-only responses, which the SDK
could not expose to structured artifact extraction. This related adapter gap
is covered with the actual FastMCP and SDK result wrappers, not a hand-written
successful trace alone. SDK loss of `isError` is also covered: validation
receipts independently force failed attempt classification.

`TaskService._image_storage_key` selects only active successful attempts when
logical dependency evidence exists. Failed/superseded attempts cannot supply
the final object key. Product completion's existing atomic Message / Task
Result / Generation / credit / Task / Run transaction and SSE contract are
unchanged. The pre-validation call incurs zero gateway calls; one successful
stub invocation results in one charge. Duplicate completion/queue delivery is
idempotent and does not invoke the stub again. Runtime performs no automatic
Provider retry, refund or suppression of actual Provider calls.

The existing single-generation-per-Task artifact cardinality is not expanded
into multi-image delivery here. DS6 proves two successful logical requirements
pass the validator; it does not claim that this change adds delivery of all
images. Genuine multi-output association remains a separate product contract.

## Acceptance and technical debt

DS1--DS14 live in `tests/test_tool_dependency_supersession.py`, including an
isolated original-case replay through real Platform MCP business validation,
FastMCP conversion, SDK result parsing, AgentService, TaskService and SQLite
product finalization. Gateway generation/download are in-process HTTP stubs.
Successful replay must retain FAILED/SUPERSEDED attempt, satisfied dependency,
completed Run/Task, persisted Message/Task Result, associated Generation,
SSE complete and exactly one credit transaction.

Additional fail-closed cases cover independent identical inputs, absent or
invalid lineage, changed requirement, cross scope/server/tool, forward and
duplicate receipt/attempt identities, Provider/permanent categories, receipt
side-effect/identity mismatch and content-free metadata.

TD-043: REQUIRED_TOOL_DEPENDENCY_RETRY_SUPERSESSION_DEBT.
Initial ACTIVE / P1; now **MITIGATED / P1** after the contract, DS1--DS14,
related regression and isolated case replay passed. Not CLOSED or
PRODUCTION_PROVEN: neither real model token uptake nor Production generation
is exercised in this work. Existing unrelated technical debts are unchanged.

### Verification evidence (2026-09-29)

- Core supersession / Task finalization / Run Trace / Grounding: 107 passed.
- Expanded related regression: **254 passed, 0 failed, 0 skipped**, 2 dependency
  deprecation warnings; duration 164.74s. Linux Python 3.11.16, marked temporary
  SQLite/local-storage fixtures; no external database or Provider.
- Suites: tool_dependency_supersession, task_finalization, run_trace,
  grounded_writing_runtime, security, agent_execution,
  agent_runtime_test_lifecycle, activity_plan_runtime,
  activity_plan_document_generator, activity_plan_template_selection,
  activity_planning_skill_v1, storage, stop_generation, api,
  wechat_official_account_agent_v1, runtime_profile, skills, skill_registry.
- XML: `release-evidence/required-tool-supersession-20260929/regression.xml`
  (local evidence, intentionally not bundled into this code commit).
- Source was a Git-mode-preserving archive plus this task's patches in an
  independent Linux snapshot, `/tmp/required-tool-retry-Bxb21D`. WSL clears its
  temporary mount on restart, so each run recreated only this task's snapshot.
- Direct Windows pytest was unavailable because the existing Linux isolation
  checker requires `os.getuid`; no test-isolation guard was modified.
- Replay executes both `3:4` and `4:5` through actual business validation /
  FastMCP / SDK parsing; the corrected call copies the new contract's explicit
  receipt. No claim is made that the historical unlinked pair now passes.
- Invalid first attempt: zero stub gateway calls/downloads. Corrected second
  attempt: one stub gateway call and download, one associated Generation,
  persisted final response/assistant message, completed Task/Run, SSE complete.
- Repeated completion and duplicate queue execution: one charge of 20 credits,
  one Message/Task Result/Generation, no additional stub invocation.
- Actual Provider calls: zero. Original Task/Run/Asset and Production untouched.
- No frontend, Skill, Prompt, Agent configuration, schema/migration, deployment
  script, candidate build or Production deployment change.
