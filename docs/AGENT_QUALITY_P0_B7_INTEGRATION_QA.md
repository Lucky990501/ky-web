# Production business P0 integration — independent incremental QA

## Scope and identity

- Input Source: `b7f99693cdb56d2a2fdf5c9cb82e04d53eb33d6b`.
- Input Tree: `45ddfb187907cceec018578d08e67c0d3811ddd7`.
- Input Parent: `66c25e5e8a518a72037f7f130d09bf6039052bc2`.
- Verified ancestry: `d9063ee43bc65e821457221ad824f5dde84fe408` → `66c25e5` → `b7f9969` → the commit containing this report.
- Isolated branch: `codex/agent-quality-production-p0-integration-v1`.
- This is NOT a Production release qualification or native PG/Redis/Worker E2E.

## Reproduced integration defects and minimal fixes

1. `hydrateIllustrationBriefs` required inline `message.structured_result` before requesting the authoritative Task. Real `conversation_detail` does not embed that envelope. Therefore valid saved plans lost the illustration entry after refresh while Word could recover separately. Recover by trusted Task ID, require completed Task / matching assistant message / valid envelope, retain principal checks and explicit fee confirmation. Cached briefs must also have a valid completed structured Task.
2. `messageGenerationHtml` printed requested and actual pixels but ignored `dimension_match=false`. Add an explicit mismatch notice, also detecting disagreement directly from recorded pixels. Keep the original image; do not resize, regenerate or retry.

Both were first independently reproduced as failing assertions. Only `workbench.js` production code changes; no backend/schema/Skill/Agent/model/Provider configuration changes.

## Independent results

| Suite | Actual result | Evidence boundary |
| --- | --- | --- |
| Targeted Python | 81 PASS, 0 FAIL/ERROR/SKIP | Private Test-host SQLite/ASGI, model and HTTP doubles |
| Targeted routing/gallery/brief components | 9 PASS | Actual workbench JS, minimal DOM boundary |
| WeChat manual export components | 15 PASS | Actual component and clipboard boundary doubles |
| Existing business P0 Chrome | 27 PASS | Local loopback HTTP/SSE fixtures, real Chrome |
| New actual backend-to-browser bridge | 34 backend assertions + 10 Chrome checks PASS | Actual MCP, SDK artifact extraction, TaskService, API, SSE, history, authorized original downloads; Provider/model doubles |
| Actual attachment API | 13 PASS | Private ASGI uploads/download/delete, normal-member and Tenant/User denial |

Total: **189 independent cases/checks**, not 189 real model executions. Prior 445 and developer 301 are historical evidence, not added to this count. Initial sandbox loopback restriction and test-fixture construction failures are not application PASS; successful final runs are separately retained.

The new bridge transports actual ASGI responses over approved Test SSH stdio to a local HTTP proxy; it does not substitute a hand-written API payload. No shared Test service, website port or deployment is used. Runtime is explicitly simulated; native Skill opening/model adherence remains unverified.

Verified:

- Two independent front/back images survive real persistence, GET, SSE and history; each previews and downloads with original pixels.
- Partial success retains one image and a truthful delivery warning, without automatically repeating a paid request.
- A requested `864x1536` versus actual `1024x1024` remains distinct and visibly mismatched.
- Real synthetic transparent Tenant Logo is programmatically overlaid; original assets and raw Provider output are preserved by the existing backend tests.
- Three activity turns clarify only missing time/location, then save the formal envelope and three briefs. History without inline JSON restores Word entry and briefs.
- Displaying or reviewing illustration fees sends no image Task POST. The existing browser contract checks exactly one Task after explicit confirmation.
- Browser GET/SSE recovery leaves private database counts unchanged: Tasks 6, Run traces 6, generations 4, credit transactions 6. These are synthetic fixture records, not customer/provider evidence.
- Actual Python Skill-style HTML survives frontend sanitization and sandbox preview, including warm yellow, 28px introduction spacing, 4px quote border and an authorized original image. WeChat editor paste/save remains NOT_VERIFIED.

## WeChat Skill 1.0.1

Read the complete original instructions and referenced style data. The candidate package passes bundled identity checks and clean isolated Registry resolution:

- `SKILL.md` SHA256: `24eaee24910a1e8beb621791c92a3fa51db6bc71bc3c99f96676fcfe0aa9e793`.
- `references/style.json` SHA256: `45af55b910f40a910f30e7384ba8f91db170a0f1345f6cac6f8eac73ba936b50`.
- ZIP SHA256: `6b37d4769cad4a8e0301a2b8c01fd90ca7ab8e0229c3e8df1e46d8a9d525a03b`.
- Bundled manifest SHA256: `a6c41a7fdf861dcaea85e25c2dca7b8038e0e295650ff1cc27843760886b2b5d`.
- `bootstrap_default=[]`: no silent Production binding replacement.

Formal control-plane APIs create/validate a private Draft binding to 1.0.1. Publish without the matching real Runtime Test is independently rejected with `Runtime Test Pending`. The existing simulated Task test confirms the resolved Skill snapshot and disabled Grounding. Ephemeral fixture Agent/Revision IDs and fingerprint are retained in `skill-evidence.json`; they are NOT installable shared-Test identities.

**Live Native discovery, actual Runtime Test, formal Test publication/enablement and Production binding: NOT_VERIFIED / BLOCKED pending a legitimate candidate-bound Test scope and budget.** The default draft creation script still requests 1.0.0; upgrade requires an explicit 1.0.1 Draft binding through the existing control plane, not mutation of a Published Revision.

Official WeChat draft-box writing is not part of the package/manual export path and was not invoked. No claim of integration or draft creation.

## Remaining contract gates

- Multiple references: API rejects more than one before accepting a Task; frontend can stage 2–5 independently but blocks submission. It does not silently truncate.
- **One reference + multiple outputs**: actual MCP preflight rejects `reference_batch_contract_required`, no HTTP transport or generated rows. Not delivered by this batch.
- 01 reported a separate ordered multi-reference successor; it is NOT merged, installed or independently accepted here. Its migration/PG/native/Provider proof remains a separate P0 batch.
- Real `gpt-image-2.5-sunburst-c` sizes/precise ratio, multiple-reference support, logo with actual generated output and model quality: NOT_VERIFIED. Mock `864x1536` acceptance is not a Provider capability claim. TD-055 stays independent/open.

## One proposed real acceptance scope — not authorization

06 must first seal the exact successor Source/Tree, compatible predecessor/install/restore evidence, dedicated Test scope, actual Member and Agent/Revision/Skill bindings, Test-only credentials, resource isolation and fee guard. Shared PRIMARY was read-only and still resolved to `20261008-d8a7814-wechat-personal-v1`; it is not this candidate. No matching b7 Source-bound authorization was found in the checked existing Test contract/source-binding records.

Proposed Tenant: existing `internal-test-staging-v1`, only after confirming scope availability and ordinary-member ownership. No fabricated Member/Revision IDs; 06 must return them before execution.

| Task | Purpose | Upper-bound credits |
| --- | --- | --- |
| Image 1 | No reference, original synthetic Tenant Logo, front/back two outputs | 20 |
| Image 2 | Same conversation, explicit 9:16, original-pixel readback | 20 |
| WeChat | Official Runtime Test bound to Skill 1.0.1, inspect resulting HTML | 5 |
| Activity 1 | Missing fields, only clarification | 8 |
| Activity 2 | Supply missing fields, formal plan and briefs; no image confirmation | 8 |
| Copywriting | Education decision-oriented expression, no fabricated facts | 3 |

Proposed total **Text requests ≤20, Image requests ≤3, application credits ≤64, automatic paid retries 0**. Requests mean actual upstream attempts, not Tasks. Dedicated Test credentials only; current settings/prices and a monetary cap must be approved separately before any call. Recalculate credits from the exact installed metadata if it differs; do not silently exceed this cap. WeChat formal Publish/instance enablement only after real Runtime Test PASS and within the separately sealed Test scope. No Production publication.

Stop on identity, binding, model, budget, credential-source or dimension-contract anomalies. Preserve Task/Run, current signed Task scope (sensitive material redacted), requested size, raw width/height, storage hash, download, history, SSE terminal state, credits and actual upstream attempt/cost ledger.

## Handoff / recommendation

- 03: consume the two minimal frontend corrections; do not recreate or overwrite its source worktree. Existing upload staging remains visibly blocked for >1 references.
- 01: own the separate ordered-reference contract and the remaining reference+multi-output gate; do not call this batch multi-reference-complete.
- 06: prepare the exact candidate-bound isolated real-Test scope, ordinary member, formal WeChat 1.0.1 Draft/Runtime Test and Provider budget controls; no shared PRIMARY switch by 04.
- 02: may review this successor's code/QA for a controlled release qualification, **not RELEASE_READY**. Re-read actual Production release-current/Exact Predecessor, verify accepted lineage and artifacts, await Owner decisions on blockers and the real acceptance scope. Do not combine with lightweight-release-V1 work.

Recommended phased delivery: first the independently verified non-multireference fixes after real acceptance and formal gates; retain ordered multireference and reference+multi-output as explicit unfinished P0 scope for a separately approved batch. Production and shared Test remain unchanged; real Text/Image calls **0/0**. No Push or deployment by this task.
