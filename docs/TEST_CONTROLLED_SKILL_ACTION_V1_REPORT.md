# Test-only Controlled Skill Action Entry V1

## Outcome and authority boundary

`TEST_CONTROLLED_SKILL_ACTION_READY` — **Source-level implementation and offline
deterministic verification only**. This does not mean PRIMARY acceptance,
installation, activation or an executable live approval is complete.

Branch: `codex/test-controlled-skill-action-v1`, based exactly on
`b0e96dfd6dfa3d3b71f5b27cb4229eb4905f6092`.
Post-commit Source/tree and file identity are recorded in the separately
generated `.codex-controlled-skill-action-v1/controlled-source-seal.json` and
the final handoff. The seal is generated after commit to avoid circular identity.

PRIMARY/Production changes = **0**; real Provider/Image/WeChat calls = **0/0/0**.
No connection to PRIMARY; no admin/Agent/Skill provisioning, venv install,
live Binding activation, real PREPARE, DB/Redis mutation, deployment or restart.
The SQLite writes and child outputs in tests are isolated synthetic fixtures.

## Changed files

New:

- `enterprise_agent_poc/app/controlled_skill_action.py`
- `enterprise_agent_poc/integrations/controlled-skill-action.v1.json`
- `enterprise_agent_poc/tests/test_controlled_skill_action.py`
- `enterprise_agent_poc/scripts/verify_controlled_skill_action.py`
- `enterprise_agent_poc/scripts/seal_controlled_skill_action.py`
- this report

Small orchestration changes:

- `app/agent_runtime_test.py`: internal controlled entry, not an HTTP route.
- `app/product_service.py`: internal ticket bridge and lost-ticket fail-closed.
- `app/product_store.py`: atomic Task reservation, no Schema/migration.
- `app/service.py`: explicit zero-model branch inside existing AgentService,
  shared Run persistence and diagnostic handling.

Append-only follow-ups in `SKILL_REVISION_RUNTIME_DISPATCH_V1_REPORT.md` and
`WECHAT_SKILL_PRIMARY_RUNTIME_ACCEPTANCE_V1_REPORT.md`. Their pre-existing dirty
content is preserved and excluded from this commit. Other dirty files are not
staged; historical untracked evidence is not cleaned or committed.

## Controlled entry contract

The existing `AgentRuntimeTest` orchestration gains:

```python
await runtime_tester.run_controlled_skill_action(session_token, {
    "tenant_id": "<registered synthetic tenant>",
    "agent_id": "<real productized Agent UUID>",
    "skill_key": "wechat-html-draft",
    "revision": "<exact published Skill Revision UUID>",
    "action": "PREPARE",
    "input": {"title": "...", "digest": "...", "html": "...",
              "cover_asset": "cover.png", "assets": {"cover.png": "<base64>"}}
})
```

The trusted native Test harness mounts a `ControlledSkillActionEntry` on
`runtime_tester.controlled_skill_actions`, using the existing SessionIssuer,
RuntimeTokenIssuer and versioned fresh isolation guard. Session token and article
remain in memory, never argv/audit/Run trace. No new service, public endpoint,
Skill API, Worker, queue or Python runner is created. Direct script execution
is not the final entry. The entry cannot be selected by chat text or public
request fields.

`environment` must be exactly `test`, checked before Task/DB mutation. Every other
environment, including Production/staging/development, returns
`CONTROLLED_SKILL_ACTION_NOT_ALLOWED`. Missing entry installation also rejects.

No `latest`, draft Skill Revision, filesystem Skill path, Python executable,
script, env or argv selector. Business input is validated again by the existing
sealed MCP adapter. The requested published UUID is matched to the exact current
Agent context/Binding/package. Missing/mismatched Revision, package, adapter,
lock, receipt or interpreter stays fail-closed under the unchanged dispatcher.

## Authorization and activation prerequisites

Native approval location is fixed:
`/etc/enterprise-agent-integrated-test-v3.3/controlled-skill-action.v1.json`.
It is **not installed by this task**. A caller cannot pass an approval path or
approval object in an execution request. Native loading requires Linux,
root:root immutable regular file, no symlinks and no group/world-writable
ancestors; exact clean Source/tree is verified. There is no permissive default.

The strict approval schema binds:

- contract/environment/authority ID;
- exact registered Synthetic Tenant and Admin UUID/email;
- approved fixture SHA and controlled Source/tree;
- exact old Dispatch Source/tree/contract SHA;
- fixed PRIMARY MCP `http://127.0.0.1:18101/mcp`;
- exact allowed action probes and integer Provider/Image/WeChat budgets of zero.

Fresh guard attestation must be <=30 seconds old and bind PASS, Test environment,
this contract, exact fixture identity, exact **new API and Worker Source/tree**
and the separately sealed **b0 MCP Source/tree**. A legacy/no-op guard, stale
receipt, wrong fixture or mixed old Worker is rejected before Task creation.
The guard must be supplied by the existing trusted native Test authority; a
dictionary from an HTTP user is not accepted as an installation mechanism.
06 must approve the new compatibility in a versioned Test contract, not rewrite
the old sealed v1/v2 contract to claim it was already approved.

Caller authentication uses the existing signed product session with current
credential-version validation and current enabled DB identity. The exact
registered user remains `users.role=member`; only the existing platform_admin
grant gives platform authority. There must be exactly one grant and it must
belong to that approved user. Ordinary member, foreign tenant, expired/revoked
session, second/unknown admin or missing guard returns
`SKILL_CONTROLLED_ACTION_AUTH_BLOCKED`. No credential is output or committed.

## Formal Task/Run semantics

Authenticated control -> **ProductStore.create_task** -> **TaskService.execute**
-> **AgentService.run** -> existing signed Task scope + runtime MCP bearer
-> **Platform MCP skill_action_execute** -> existing SkillActionDispatcher
-> exact Revision Runtime -> normalized result/Skill receipt
-> existing atomic Task result/message/credit/Run completion persistence.

Creation is atomically reserved as an ordinary `running` Task with a durable
controlled marker. This avoids the ordinary queue selecting a queued Task
between reservation and execution. There is no new status enum or Schema.
Recovery or accidental queue delivery without its internal authorized ticket
creates a formal failed Run/Task, never a Model call. Duplicate completed
delivery does not repeat dispatch. Persistence-only recovery uses the existing
completion path, not another Skill execution.

Success: Run `runtime_completed` -> atomic persistence -> Run/Task `completed`.
Failure: formal Run/Task `failed`; typed safe diagnostics, existing Skill failure
receipt when reached, and a persisted controlled diagnostic receipt/audit when
the transport or entry fails earlier. Cancellation uses existing cancellation
states. No stdout/stderr/env/absolute interpreter or Skill path is returned.

The conversation needs a non-null logical persistence key; its runtime version
is `controlled-skill-action-v1` and key is `controlled:<run UUID>`, not a Codex
thread. Run's Codex thread is NULL. Trace explicitly identifies
`execution_kind=controlled_skill_action`, model/provider/reasoning=NULL,
`CONTROLLED_TEST_ACTION=true`, Provider/Image/WeChat=0. RuntimeProvider is never
accessed in this branch (including startup-events/error paths).

Result includes the existing status/action/artifact_refs/summary/verification/
receipt_ref contract. MCP content is matched to the actually persisted Skill
receipt with Task/Run/tenant/Agent/action/Revision/Dispatch identity. Successful
artifacts are revalidated against contained shared Task workspace paths and
actual SHA/size; offline=true and wechat_calls=0 are mandatory. A forged MCP
response without the correct persisted receipt is not successful evidence.
The normalized JSON is persisted as the formal Task final result and in Run
trace; no new structured-result or WeChat business contract is introduced.

PREPARE uses the original action permission and never resolves WeChat Secret.
CREATE_DRAFT is a **negative permission/credential probe only** in V1: it reaches
the unchanged gate, and the existing registration remains execution-disabled.
It cannot execute even if a credential exists. Unknown or extra required tools
are not fabricated as completed dependencies.

Audit records environment, tenant, Agent, Skill/exact Revision/action, Task/Run,
actual runtime identity when resolved, normalized result, caller/authority,
approval/fixture/Source identity and zero-call markers. Earlier failures record
an unresolved runtime identity rather than inventing one.

## Source compatibility, not identity rewriting

Three separate sealed layers:

| Layer | Source / identity |
| --- | --- |
| controlled API/Worker orchestration | this branch's post-commit Source/tree |
| existing native Platform MCP dispatch | `b0e96dfd6dfa3d3b71f5b27cb4229eb4905f6092` / `aea4cfc6ae6990f08c6c7f860678bab3d85a40e4` |
| immutable revision Python Runtime checkout | `e7e96b1a959d8631dc9dcd5c24939fa483ac134a` / `a86b9e4d2f4785f1cbef5ae5e654c54cf20a5f75` |

The controlled Source calls the **existing** MCP transport; it does not run
`from_settings` on a mixed current checkout and pretend its old b0 seal passes.
MCP still verifies its original b0 dispatch seal and e7 runtime compatibility.
No second MCP listener/shadow service is authorized. The old 18-file dispatch
contract, 41-file Runtime descriptor, Native 13-file artifact, lock, adapter
digest, old seals, Secret contract and WeChat business source are unchanged.

## Offline tests

Targeted Windows tests only; not Full pytest/Stage2/Candidate or Linux acceptance.
Tests substitute Settings before imports (no `.env` read), use isolated marked
SQLite and real Registry/Binding/permission/AgentService/TaskService/FastMCP/
persistence. Only HTTP, Revision interpreter and child process are mocked for
controlled execution. An explosive RuntimeProvider rejects every access.
The historical Agent eligibility fixture is produced by a clearly synthetic
mock Codex turn using the existing Runtime Test lifecycle; it is not real
Provider evidence, not PRIMARY evidence and not created by the controlled entry.

- **33 new controlled cases PASS**;
- **31 existing dispatch cases PASS**;
- **66 existing WeChat adaptation/permission cases PASS**;
- **29 existing revision-runtime cases PASS**.

Total: **159 unique cases; 0 failures/errors/skips**. The original 17 retained
workflow cases are part of the 66, not counted a second time. Simulated draft
messages in the old suite's console are mocked WeChat responses, not API calls.

Required 16 scenarios all covered: valid Test, Production deny, missing Binding,
missing Revision, actual package identity mismatch, runtime unavailable,
no-secret PREPARE, no-secret CREATE_DRAFT blocked, Provider zero, formal Task/Run,
success completed, failure failed, normalized result, persisted receipts,
path selectors rejected, ordinary chat unaffected. Additional coverage includes
member/foreign tenant/second admin denial, missing authority, exact UUID,
wrong client, real recovery/lost ticket, disabled Agent, transport-error
redaction, revoked session, forged result, completed duplicate delivery,
other required-tool denial, current permission revocation and stale/mixed
authority rejection.

## Observations / 06 handoff

1. **CONTROLLED_ACTION_REQUIRES_EXISTING_EXECUTABLE_AGENT**: V1 deliberately
   preserves the published/enabled/current formal Runtime Test gates. It creates
   no `agent_template_tests` row and cannot promote deterministic Skill acceptance
   into Codex quality/publish eligibility. A new configured/draft Agent or an
   empty environment is not executable by this V1. If the next acceptance needs
   a new unqualified Agent with zero Provider, STOP for an explicit non-quality
   test authorization contract; do not seed fake passed tests or enable by SQL.
2. **VERSIONED_CONTROLLED_ACTION_ACTIVATION_NOT_EXECUTED**: new Source and exact
   mixed-layer compatibility require separately approved native authority/guard
   installation and fresh attestation. READY here grants no install/provision
   authority. Do not apply old sealed approval to the new Source silently.
3. Future authorized 06 sequence: fresh attestation -> approve/load the exact
   controlled Source+tree and original b0/e7 identities -> native guard/approval
   binding -> existing eligible Agent and exact published Skill binding ->
   interpreter/lock/receipt read-only verification -> one separately authorized
   controlled PREPARE -> Task/Run/result/receipt/artifact verification -> STOP.
   If any prerequisite is absent, report it; no Provider task to close it.

No technical-debt item is silently marked CLOSED/PRODUCTION_PROVEN. No public
route, Skill/Prompt/Model, Secret contract, Revision Runtime, Schema/migration,
Production release tooling/config or sealed v1/v2 identity changed.

**PRODUCTION_UNCHANGED · PRIMARY_UNCHANGED · Provider/Image/WeChat=0/0/0. STOP.**
