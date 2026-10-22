# V6 B1 candidate — dual portals and formal copywriting

Status: implementation candidate; **not deployed, not Production release-ready**.

## Identity and scope

- Production compatibility base: `d9063ee43bc65e821457221ad824f5dde84fe408`.
- Preview reference only: `bccf7f4cce3d57de65050be9f13dffbf6740fd27`.
- Branch: `codex/v6-dual-portal-b1` (isolated worktree; no Preview merge).
- Approved architecture document: `WORKBENCH_DUAL_PORTAL_PRODUCTIZATION_V6_PLAN.md`, SHA256 `125a87788dd1ae46b9a93de1fe0d11bb1aa64fa1fbb8ba7e61241f9786ffe5c8` (01-owned source; not edited/copied into this commit).
- Exact candidate Source is the commit containing this report, obtainable with `git rev-parse HEAD`; final execution receipt supplies the SHA.

No online commands, customer account changes, budget changes, Preview database copies, migrations, model switches, immutable Skill changes or Provider calls. Formal model configuration remains the base configuration, not Preview Flash.

## Entry and permission contract

| Entry | Purpose | Authority |
|---|---|---|
| `/manage` | Agent Workbench | Existing platform admin or current-tenant enterprise admin |
| `/manage/agents` | Agent management / enterprise enabled capability list | Platform editing remains platform-only; enterprise admin sees own enabled capabilities |
| `/manage/skills` | Existing Skill registry | Platform admin only |
| `/manage/enterprise`, `/manage/members`, `/manage/knowledge`, `/manage/assets`, `/manage/usage` | Existing enterprise management, members, knowledge, assets and recent usage/diagnostics | Existing enterprise admin APIs; no platform privilege escalation |
| `/workspace` | AI Workspace | Existing formal authenticated user |
| `/workspace/copywriting` | Copywriting conversation and own drafts | Existing Agent enablement, Task and Conversation ownership |
| `/workspace/history`, `/workspace/profile` | Own history/profile | Existing owner-scoped APIs |
| `/legacy/workspace` | Prior all-agent dashboard | Compatibility entry; old `/agents/*`, `/platform/*`, `/conversations` etc. remain |

Management navigation excludes daily creation. Customer navigation excludes management, including for admins; an explicit portal link is separate. Public HTML is a login shell, not authorization. Expired management sessions return that shell; all protected API requests still reject expired credentials. Platform admin status does not grant another user's history or enterprise-admin role.

B1 customer homepage exposes only the formally enabled copywriting entrance. Image/campaign/WeChat status is derived from actual authorized metadata and explicitly states that migration into AI Workspace is not included in B1.

## Selective transplant / formal data contract

| Preview reference | B1 adaptation |
|---|---|
| `preview_copywriting_prompt.py`, `copywriting-video-script.md` | New tenant-neutral `app/copywriting_context.py`; no company-specific facts, Preview installer, environment switch or temporary authorization |
| Full-draft editing context | Read formal `tasks` + `task_results`, joined through real Conversation/Tenant/User ownership; preserve all completed user turns and complete draft versions |
| Simple conversation experience | Reuse formal V2 conversation UI, SSE, cancellation and histories; add portal nav, natural-language “继续修改”, protected TXT export and bounded auto-resizing composer |

Business prompt identity: `copywriting-business-v6-b1.1`, plus SHA256 written into each new ordinary copywriting RunTrace. `CATALOG`/published Skills are not rewritten. Direct administrative Runtime Test and productized immutable execution contexts retain their original instructions and execution contracts.

The existing normal Runtime `thread_resume` does not apply new developer context. For ordinary copywriting continuation only, the service uses the already-supported `create_session` API with canonical owned full drafts, then atomically replaces the thread pointer on the same formal Conversation. Old RunTrace/thread identities and results remain untouched. There is no SDK patch, new runtime or intent router. The latest request wins; earlier full drafts remain available for explicit reference. Previously generated claims are not promoted to verified enterprise facts.

Context encoding is bounded at 262144 UTF-8 bytes for this context contract. Nothing is silently cut to 12000 characters. Over-capacity requests fail before session/Provider start with `copywriting_context_limit` and a non-retry-oriented message. This is not a claim that arbitrarily long histories fit a model's context.

New copywriting assistant messages and canonical results store full text. Historical truncated message projections are hydrated read-only from the same owner's completed canonical result. `response.incomplete` / failed Runtime turns do not become successful results. Whole-version copy uses the full persisted text, and `GET /api/v1/tasks/{task_id}/text` returns the same UTF-8 text with attachment/no-store/nosniff headers after formal ownership checks.

## Submission, concurrency and recovery

- Optional copywriting `client_request_id` binds a deterministic Task ID to Tenant/User and a fingerprint of Agent/message/original Conversation.
- Receipt is stored in existing `execution_events`; no schema additions. Same ID + changed input rejects. Same request reuses Task identity.
- SQLite transaction / PostgreSQL advisory lock serializes same-key creation. Conversation row locking plus active-task check serializes edits to one draft while independent projects remain independent.
- Existing Redis enqueue path remains. A copywriting-only atomic queued-to-running claim prevents duplicate workers from issuing duplicate turns. Existing persistence-only recovery and task-scoped debits remain.
- A crash after claim with no known Run is deliberately **not** automatically resent: outcome uncertainty requires diagnosis/cancellation, not paid retry. A same-Conversation live task is blocked until terminal/cancelled.
- Current project draft text is held only in bounded in-memory, principal-scoped UI storage. Logout/identity change clears it. New conversation binding moves the draft key. Page switch detaches observers, not backend work.

## Zero-cost evidence

All outputs in the following tests are explicitly synthetic Fixture outputs. They prove transport, ownership, complete persistence and UI behavior, **not actual model writing quality**.

1. `tests/test_dual_portal_b1.py`: five real ASGI submission turns through formal TaskService/AgentService with scripted Runtime; >12000-character draft, all prior versions, SSE completion, restart/redelivery, single debit, context rejection, incomplete/failed responses, correct/wrong/expired login, role boundaries, same-/cross-Tenant ownership, own full TXT export.
2. Related base regressions: `test_product_auth.py`, `test_task_history_ownership.py`, `test_workbench_routes.py`. Selected set: **75 PASS, 1 raw SKIP**. Skip is Linux Node availability, not Provider or unresolved backend failure.
3. Actual `workbench.js` Node suite on Windows: **117 PASS**, including new customer/management navigation and old all-agent dashboard through `/legacy/workspace`. This separately closes the Node availability skip.
4. `tests/dual_portal_b1.browser.cjs`: independent headless Chrome, real static files and mocked HTTP. Five submissions, matching Conversation, full copy, actual TXT file download, natural-language edit action, history reload without new tasks, draft restore, role navigation, desktop 200px/mobile 160px textarea, no JS exceptions.
5. Browser screenshots (Fixture): `docs/evidence/v6-b1/b1-copywriting-desktop-FIXTURE.png`, `b1-copywriting-mobile-FIXTURE.png`, `b1-management-FIXTURE.png`.

Initial Windows pytest could not use the existing POSIX ownership guard. Initial WSL tests on Windows-mounted source rejected immutable Skill file modes. Neither guard nor Skill was weakened: `scripts/b1_local_fixture.sh` stages Git bytes/modes in a **new local Linux temporary directory** and uses an existing interpreter for the above four selected files. No full pytest, Stage 2, Candidate Build, environment upgrade, services, PG, Redis or network Provider is run. Snapshots are retained; no cleanup of historical evidence.

The browser test blocks all external network, including remote fonts, and uses a loopback-only synthetic TXT endpoint. Chrome download verification needs a permitted local process filesystem; the restricted sandbox cancelled downloads. The independent permitted fixture run passed without using any customer session. Screenshots use fallback fonts.

## Changed files

- `enterprise_agent_poc/app/main.py`: additional route shells, formal-owned TXT export, optional request identity.
- `enterprise_agent_poc/app/copywriting_context.py`: versioned tenant-neutral persona and complete owned history.
- `enterprise_agent_poc/app/service.py`: ordinary copywriting context binding and prompt trace; other Runtime paths preserved.
- `enterprise_agent_poc/app/product_service.py`: business-user binding, duplicate claim, explicit over-capacity feedback.
- `enterprise_agent_poc/app/product_store.py`: idempotent creation, same-Conversation protection, full copywriting messages/history.
- `enterprise_agent_poc/app/static/workbench.js`, `workbench.css`: dual navigation, copy/edit/export, history refresh and composer.
- `enterprise_agent_poc/tests/test_dual_portal_b1.py`, `workbench_routes.test.cjs`, `dual_portal_b1.browser.cjs`: targeted fixtures.
- `enterprise_agent_poc/scripts/b1_local_fixture.sh`: reproducible selected local tests only.
- This report and three Fixture screenshots.

## Remaining gates / handoff

- `PG_REDIS_WORKER_E2E=NOT_VERIFIED`: 06 must validate exact candidate Source under a separately authorized isolated native PG/Redis/Worker environment. PostgreSQL lock behavior, real queued worker lifecycle, Runtime and MCP integration have not been represented as PASS here.
- Real five-turn language quality / fact preservation / MCP enterprise search require 04's exact-Source test with a newly scoped Provider budget. B1 fixture text is canned, not a real Provider result.
- `MIGRATION_REQUIRED=NO`: existing schema reused; none added/applied.
- B1 includes backend and business-prompt changes, so it is **not** eligible for a static-only release shortcut. 02 retains release/recovery authority; formal deployment and rollback gates remain pending.
- Prior root-worktree edits, online Production and online Preview are unchanged. This is an action-boundary statement, not a newly performed production health audit.
- `NEW_PAID_CALLS=0`. No accounts, authorizations, quotas, history or real assets changed.

Context status: complete candidate implementation and local zero-cost evidence; ready for scoped 04/06 review, not a claim of formal E2E or Production acceptance.
