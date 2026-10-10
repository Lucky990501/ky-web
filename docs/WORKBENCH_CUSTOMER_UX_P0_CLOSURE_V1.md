# Workbench Customer Acceptance UX P0 Closure V1

Status: WORKBENCH_CUSTOMER_UX_P0_CANDIDATE_READY — UI candidate only, not customer business E2E PASS.

## Source and boundaries

- Independent Feature Worktree: `customer-ux-p0-v1/ky_web`.
- Feature branch: `codex/workbench-customer-ux-p0-v1`.
- Exact parent Application: `28e061a114118a27609330125c756e193d3248a6`.
- Parent Tree: `8ff5e4a8ed26c32ba040b4fa27cf1d380f3c7541`.
- New Source/Tree are the Git object containing this report (`git rev-parse HEAD`, `git rev-parse HEAD^{tree}`). Final handoff records fresh remote matching values; a commit cannot embed its own hash.
- No changes to 01 Selector worktree, 02 integration worktree, PRIMARY directory/config/DB, Production, Runtime, Skill, Prompt, Secret, Guard, Approval, Schema or Migration. Existing root dirty files are not included.
- Actual Provider / Image / WeChat / CREATE_DRAFT calls: **0**. No live API access or deployment this round.

## Real 28e contract audit

Source, not historical reports, establishes the following:

| Capability | Existing 28e source | Finding |
| --- | --- | --- |
| Agent eligibility | `app/main.py` agent_metadata | Tenant-filtered enabled Agent or HTTP404; frontend must not synthesize publication |
| Assistant body/history | Task/SSE final_response + assistant_message_id; conversation messages.content | Available for a generated-body preview |
| Account state | `GET /api/v1/profile/wechat-account` | Configured/verification_status read; no secret/token sent to article UI |
| PREPARE output | `app/wechat_prepare_action.py` normalize | workspace artifact_refs, verification.offline=true, visual_verified=false; not public HTML |
| Draft permission | `app/platform_mcp/server.py` wechat_create_draft_authorize | Permission receipt only, NOT_EXECUTED is not a draft |
| Draft execution | `app/skill_dispatch_config.py` registrations | CREATE_DRAFT enabled=False; no public customer execution route |
| Public artifact reader | `app/main.py` route inventory | No prepared-HTML endpoint with user/Tenant/message ownership contract |

**BACKEND_PENDING** remains the official integration state. No guessed HTTP endpoint, ordinary Chat proxy, workspace/storage path fetch, automatic PREPARE, or Secret mutation has been added.

## WX-05 completed UI scope

- One shared `WorkbenchWechatArticle` component, selected only by stable Agent slug.
- Preview/draft entry added to persisted assistant messages and new terminal SSE/poll rendering; not to user messages, partial/cancelled replies, other Agents, or unbound message IDs.
- HTML or Markdown generated-body preview. It is explicitly labelled **not PREPARE-verified, not uploaded**. It does not claim to display inaccessible prepared.html.
- Inert template sanitization: tag/style allowlist, remove scripts/forms/frames/SVG/MathML and event/URL/identity attributes. Text, headings, paragraphs, lists, quotations, tables and safe color/spacing are retained.
- Preview iframe has empty sandbox, no scripts or same-origin allowance, no-referrer and CSP default-src none. All images/links/network resources are disabled. No untrusted document can read the host document.
- Fixed bounded modal, internal iframe scrolling, 1440/1920/375 and landscape coverage; visible close/back controls.
- Explicit “创建微信草稿” button, default disabled. Missing configuration, not connected, disabled/404 Agent, denied permission and BACKEND_PENDING have distinct safe feedback.
- Trusted adapter boundary implements explicit confirmation, loading, double-submit lock, fresh metadata/account/permission recheck immediately before execution, exact receipt validation, sanitized failures, manual confirmed retry and stale-view rejection.
- Only a confirmed server-normalized created receipt with matching Agent/message and valid draft_media_id + receipt_id is shown as success. Permission resolution, empty IDs, wrong-message results and unconfirmed responses cannot become success.

## Adapter boundary is NOT an existing server API

The default adapter always returns BACKEND_PENDING and never performs a write. Isolated tests inject a fixture adapter **only in the test runner**, not through URL/query flags, localStorage, model HTML or a product debug toggle.

Before a later authorized integration, 01/06 must supply and approve a real session-authenticated port:

1. `availability({agentId,messageId})`: READY + exact identity + can_create_draft=true only after server-side user/Tenant/Agent Publish/Enable/Revision/Skill/action/connected checks; DENIED or BACKEND_PENDING otherwise.
2. `createDraft({agentId,messageId,userConfirmed:true})`: server reads immutable owned article/PREPARE artifact; client provides no Tenant, HTML, secret, executable or object path.
3. Server validates current authorization again, binds confirmation to article revision, handles CSRF and persistent idempotency, and normalizes an actually successful WeChat response/readback to `{status:'created',confirmed:true,draft_media_id,receipt_id,agent_id,message_id}`.
4. Server must provide authoritative prior receipt/state on refresh; frontend in-flight locking alone is **not** cross-refresh exactly-once delivery. Ambiguous network failure requires checking draft state before retry.
5. A separate owned artifact reader is needed to replace generated-body preview with actual PREPARE output. Do not make workspace refs or arbitrary HTML permission/capability grants.

This is a proposed normalized UI port, not a declaration that 28e already implements these fields/routes. This commit does not enable it.

## UI-05 closure

Profile editor now uses the existing shared modal lifecycle: semantic dialog, Escape close, Tab/Shift-Tab wrap, inert background, escaped focus redirection, close/cancel/mask focus restoration and original inert-state restoration. Successful fixture form save uses the unchanged PUT /api/v1/me and restores focus to the newly rendered trigger. Late avatar/save callbacks cannot write removed dialog DOM. Password/help/image viewer adjacent behavior remains covered.

## Deterministic verification

- Frontend Node suite: **97 passed, 0 failed, 0 skipped** (90 existing + 7 direct tests).
- Customer UX browser: **26 named scenarios PASS_AUTOMATED, 0 failed**; see `WORKBENCH_CUSTOMER_UX_P0_BROWSER_RESULT_V1.json`.
- Existing WeChat config browser acceptance 1–9: PASS, 1440/375 PASS.
- Existing reference-image browser acceptance A–F: PASS, including click/drop/paste and 375px.
- `node --check` both product JavaScript files: PASS; `git diff --check`: PASS.
- Local native headless Chrome, real renderer/keyboard but fixture APIs/adapter. Normal form PUT and Chat/SSE are synthetic too. External transport blocked; existing Google Fonts attempts aborted, not counted as successful external calls.
- XSS corpus + opaque-origin host denial; exact receipt/permission/404/config gates; confirm-cancel, loading/double submit, failure/manual retry, stale callback; long iframe scrolling; 1440/1920/375/812×375; adjacent modals.
- Earlier harness failures were corrected: inert is on the main ancestor, existing external font attempts are separated, and new-entry 404 differs from readable history. A real UI sanitizer issue with canonical RGB colors was fixed and regression-tested. Earlier failures are not claimed to be PASS.
- Screenshots remain local under `.customer-ux-p0-evidence/`; desktop and 375px screenshots were visually inspected. They contain synthetic data, not customer secrets.
- No Full pytest, Stage 2, Candidate Build or PRIMARY execution claimed.

## Changed files

1. `enterprise_agent_poc/app/static/index.html` — load shared article component before workbench.
2. `enterprise_agent_poc/app/static/workbench.js` — completed/history hooks, genuine connection hint, profile/common modal lifecycle.
3. `enterprise_agent_poc/app/static/workbench.css` — scoped preview/action/modal constraints.
4. `enterprise_agent_poc/app/static/wechat-article-ui.js` — safe preview and fail-closed adapter.
5. `enterprise_agent_poc/tests/wechat_article_ui.test.cjs` — seven direct component/contract tests.
6. `enterprise_agent_poc/tests/customer_ux_p0.playwright.cjs` — 26 isolated browser scenarios.
7. `docs/WORKBENCH_CUSTOMER_UX_P0_BROWSER_RESULT_V1.json` — actual deterministic result.
8. `docs/WORKBENCH_CUSTOMER_UX_P0_CLOSURE_V1.md` — this handoff.

## 02 integration handoff

Integrate only this independent Feature commit after verifying its parent/source/tree and inspecting the eight-file diff. If 02 has a different integration base, cherry-pick the single UI successor; do not merge the entire 28e ancestry as a shortcut. Preserve its backend/selector/production integration work.

Ship the new shared JS file and index load order together. Existing static build serves /static files; no release configuration was modified. Re-run isolated tests on the reconciled Source. Keep the default pending adapter until a separately reviewed formal API port exists. No deployment is authorized by this handoff.

## 06 new Test retest set

- UI-05: native deployed profile Escape, Tab/Shift-Tab, mask/background protection, focus return, successful/failed form saves, password/help/image neighbors; desktop/375px/landscape.
- WX-05: ordinary-user metadata200 after real Publish/Enable, legitimate generated article Task/Run, real PREPARE Task/Run/artifact read, faithful safe preview, permission-denied and config/connected branches.
- WX-06/P0-A: only under separately authorized Test-only successor and budget, explicit click+confirm → real WeChat create/readback; genuine receipt/media_id, no duplicate on retry/refresh, no auto-publish/group-send/delete. Re-test revoked Tenant/Agent/action authorization and wrong ownership.
- SEC-04/SEC-06: article XSS/URLs/host-context isolation and privileged action denial (existing Case IDs, no new matrix system).
- STR-01/STR-03/WB-02: completed SSE/poll, Stop partial cleanup, history refresh and route switch adjacent regressions.

Original 71-case files at the main workspace are updated with candidate evidence after publication. Their deployed/business statuses and counts remain unchanged: 23 PASS_REAL, 28 BLOCKED, 15 NOT_TESTED, 4 NOT_DEPLOYED, 1 NOT_IMPLEMENTED; business E2E PASS=0. Candidate UI PASS_AUTOMATED does not close WX-05/P0-A or the broader UI-05 accessibility case in PRIMARY.

## Skill influence and remaining conditions

ui-ux-pro-max guided visible focus, keyboard containment, touch/loading feedback and bounded mobile preview. git-publish guided exact staging/commit/remote identity verification; its deployment steps are explicitly excluded by this task.

Required upstream: ordinary-user metadata200 with genuine eligibility, owned prepared-artifact reader, approved public draft execution/availability/result/idempotency contract, Test-only CREATE_DRAFT successor and explicitly authorized real verification. Last live Round2 Metadata404 is preserved, not re-tested live in this development round. PRIMARY/Production remain unchanged. Candidate READY is not a real draft or customer acceptance PASS.
