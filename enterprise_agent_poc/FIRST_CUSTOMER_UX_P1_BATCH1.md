# First Customer UX / Delivery V1 — P1 Batch 1

**Scope:** local, isolated development only.  No migration, compatibility, release, runtime, retrieval, embedding, or production action was performed.

## Acceptance status

| Area | Status | Evidence |
| --- | --- | --- |
| A. Agent UX | PASS | Member browser acceptance, including first-use guidance, example prefill, resume guidance, copy feedback, regeneration prefill, and tenant-admin visibility, passed in the isolated preview. |
| B. Recent Tasks / Usage | PASS | The tenant-scoped API, role enforcement, filters, summary, and enterprise-admin browser acceptance passed. |
| C. Knowledge / Assets UX | PARTIAL | Knowledge upload states and evidence-backed Knowledge references are implemented and tested.  Asset references are withheld because current traces lack a safe, structured asset identity (`REFERENCE_EVIDENCE_GAP`). |
| D. Small UI Cleanup | DEFERRED | No unrelated P2 cleanup was added. |

## Delivered behavior

### A. Agent UX

- Empty conversations for `image-agent`, `copywriting-agent`, and `campaign-agent` now show a concise purpose, business scenarios, and clickable example requests.  Selecting an example fills the composer and does not submit it.
- Assistant text responses expose **复制** for the complete visible response and display **已复制** after success.  Internal task/run/trace data is not part of the copied text.
- **重新生成** restores that turn's original request in the existing composer and requires the customer to send it.  Conversation continuity is unchanged.
- Existing failure handling keeps the safe user message, exposes **重新尝试**, and restores the original input without changing worker retry behavior.
- Image result viewer/download behavior is retained; the same regeneration entry is available from assistant output.
- The normal agent workspace no longer presents Skill, MCP, Runtime, model/provider, execution-context, runtime-profile, or fingerprint information.

### B. Recent Tasks / Usage

- Added `GET /api/v1/admin/recent-tasks`, guarded by `require_admin` and scoped by `tenant_id` in the data query.
- Enterprise administrators have a lightweight **最近任务** page with 7/30-day, status, and Agent filters.
- Statuses render as 排队中、处理中、已完成、失败.  Rows include time, member, Agent, credit use, and the safe failure message; the administrator diagnostic identifier remains available through the existing safe task view.
- The summary is calculated for the selected time window independent of list filters: task count, completed count, failed count, and committed task-credit consumption.
- Members are denied both the route and API; no client-only access control is relied upon.

### C. Knowledge / Assets UX

- Knowledge now follows one clear sequence: choose file, inspect name/type/size, then **上传并处理**.  It displays waiting, processing, complete, and safe failure/retry states without exposing parser paths, database details, or raw errors.
- Conversation results render **参考资料** only when a completed run trace contains accepted Knowledge retrieval evidence with a concrete file identity.  File names are resolved under the same tenant.
- Current asset trace output contains redacted/unstructured summaries rather than a safe asset identifier.  The UI intentionally renders no **使用素材** entry in that case.  This is `REFERENCE_EVIDENCE_GAP`, not an inference from tenant inventory; it does not require an architecture change for this batch.
- Single-turn attachments remain `ATTACHMENT_UX_DEFERRED`.

## Browser evidence

- Production browser was opened only to its login screen.  No production authentication, deploy, or mutation was attempted.
- The isolated local preview used a dedicated SQLite database, data directory, and object directory.  No production data or session was used.
- **Member, Desktop:** all three Legacy Agent empty states showed business purpose, scenarios, and examples; selecting an example populated the composer without sending.  A persisted local fixture showed **复制**, **重新生成**, and **参考资料**.  Regeneration restored the original prompt and did not submit; copy changed to **已复制**.  The member drawer omitted Knowledge, Assets, Recent Tasks, and other tenant administration entries.
- **Enterprise admin, Desktop:** a tenant-only local enterprise-admin account showed the Recent Tasks summary (task count, completed count, failures, and credit use), 7/30-day, status, and Agent filters.  Selecting **已完成** refreshed the list while preserving the 7-day summary.  A direct refresh at `/recent-tasks` served the application shell and rendered the page.  The Knowledge page showed one choose-file control, no duplicate upload control, a disabled **上传并处理** action until selection, and the four processing states in its explanatory text.  The Assets page remained accessible to the tenant administrator.
- **390px:** visual checks confirmed the member Agent first-use layout and the enterprise-admin Recent Tasks summary/filter layout fit the narrow viewport without horizontal clipping.  The viewport override was reset after the checks.
- The completed-conversation browser fixture used a persisted local run trace to exercise the UI controls.  Automated coverage separately verifies that only accepted Knowledge retrieval evidence with a tenant-owned file ID produces that reference; the fixture did not claim a live provider run.

## Validation

- Targeted regression: `49 passed, 2 warnings` for `tests/test_first_customer_ux_p1.py`, product auth, history, and workbench routes.
- Full Python suite: `447 passed, 98 skipped, 2 warnings`.
- Node route suite: `28 passed`.
- `node --check app/static/workbench.js`, `python -m compileall -q app`, and `git diff --check` passed.
- The first full run found four outdated static-resource-version assertions in `tests/test_history.py`; they were updated to the P1 cache version and the full suite then passed.

## Files changed

- `app/main.py`
- `app/product_store.py`
- `app/static/index.html`
- `app/static/workbench.css`
- `app/static/workbench.js`
- `FIRST_CUSTOMER_UX_P1_BATCH1.md`
- `tests/test_first_customer_ux_p1.py`
- `tests/test_history.py`
- `tests/workbench_routes.test.cjs`

## Preserved out-of-scope worktree files

- `FIRST_CUSTOMER_P0_FINAL_SMOKE.md`
- `FIRST_CUSTOMER_P0_PRODUCTION_DEPLOYMENT.md`
- `FIRST_CUSTOMER_P0_PRODUCTION_GO_NO_GO.md`

## Remaining UX debt

- `REFERENCE_EVIDENCE_GAP`: emit a safe structured asset identity from an already-existing execution trace only if separately authorized; do not infer asset use from uploaded files.
- `ATTACHMENT_UX_DEFERRED`.
- No production action follows this batch.
