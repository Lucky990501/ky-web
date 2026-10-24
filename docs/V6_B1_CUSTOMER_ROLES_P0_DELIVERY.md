# V6 B1 customer frontend and role isolation — successor candidate

## Identity and limits

- Branch: `codex/v6-b1-customer-roles-p0`.
- Base: `801fd27a92f32ecc87e9aec1a8c253a8942d1e92` (the accepted B1 business source).
- Exact successor Source: the Git commit containing this report; obtain with `git rev-parse HEAD`.
- Independent worktree; the original checkout's seven pre-existing tracked edits are not included.
- No Production, old Preview, or installed new Test source/config/database changes. No deployment, Provider calls, permission grants, quota edits, or migration.
- Candidate only. Publication and identity-bound PG/Redis/Worker validation remain controller/06 work.

## Implemented scope

1. Authenticated non-platform identities receive 403 at `/manage/*` and legacy `/platform/agents` / `/platform/skills`. Platform APIs keep their server-side platform capability checks; legacy runtime diagnostics/Test now require the same capability. Anonymous/expired browsers receive only the common login shell, while protected APIs return 401.
2. `/workspace/settings` and brand/members/knowledge/assets/usage/agents sections require the current Tenant's enterprise-admin role. Employees use the same workspace without this navigation or API authority. Existing tenant-scoped API implementations are reused rather than duplicated.
3. Brand settings reject platform/runtime/key/credit fields. Logo changes retain the existing validated upload and tenant-bound storage contract. Member operations retain role constraints, self-protection and last-admin checks.
4. Usage shows the real current-Tenant credit balance, gross recorded debits, and grouped task counts, not private drafts. Provider account balance and personal quota allocation are explicitly not implemented.
5. Customer capability status separates migration eligibility from legacy Enabled: only B1 copywriting is eligible; campaign/image/WeChat remain not migrated, including enabled legacy rows. Disabled copywriting is not enabled; missing declared Responses/dispatch configuration is unavailable. This is release/configuration admission, **not a live Provider/Worker health probe** (`runtime_health=not_probed`). API-process secret presence is not used to infer a separate Worker's health. No productization rows are edited or published.
6. Platform navigation has management, Agents, Skills, enterprise directory, diagnostics and profile, not daily creation. Enterprise directory exposes names/member counts only; existing productization tenant-instance controls remain the enable/disable/configuration authority. No new cross-Tenant member CRUD is invented.
7. Global runtime error bodies are not exposed as a platform privilege shortcut to private task content. Platform administrators still receive 404 for someone else's conversation/export.
8. The UI/UX skill guided reuse of the existing design and a lightweight, keyboard-accessible settings navigation. Disconnected settings subviews cannot overwrite a newer route. A measured mobile asset-filter overflow was fixed only within settings. Copywriting/context/Runtime/Prompt behavior is otherwise unchanged.

## Zero-cost evidence (not public E2E)

| Evidence | Actual result |
| --- | --- |
| Selected ASGI/SQLite/scripted Runtime pytest scope | 84 passed, 1 raw skip, 2 dependency deprecation warnings |
| Raw skip | WSL has no Node for the Python-to-Node wrapper; not silently counted as pass |
| Native Windows actual JS routing/async suite | 117 passed, 0 failed; covers the skipped frontend wrapper's underlying suite |
| Isolated real headless Chrome with mocked business HTTP | PASS, 5 turns, no page errors |
| Desktop/mobile | 1440x1000 and 375x812; no horizontal overflow in tested settings; composer 200px/160px |
| Copy/TXT/draft/history | Full result clipboard and actual downloaded synthetic TXT bytes match; draft and history reopen verified |
| Roles/settings | Employee has no settings navigation; enterprise settings sections and platform-only management navigation verified in browser Fixture |
| Session expiry | Mock 401 restores login and removes settings view |
| Server isolation | Two-Tenant member/config/knowledge/assets boundaries, expired session and private history/exports tested with formal auth fixtures |
| Logo | Existing transparent-PNG upload plus cross-Tenant/read/write safety cases passed |

Artifacts:

- [Pytest output](evidence/b1-customer-roles-p0/fixture-results.txt), [JUnit](evidence/b1-customer-roles-p0/fixture-junit.xml).
- [Native frontend test output](evidence/b1-customer-roles-p0/frontend-results.txt).
- [Browser result](evidence/b1-customer-roles-p0/browser-fixture-results.json).
- [Desktop enterprise settings](evidence/b1-customer-roles-p0/b1-enterprise-settings-desktop-FIXTURE.png).
- [Mobile enterprise settings](evidence/b1-customer-roles-p0/b1-enterprise-settings-mobile-FIXTURE.png).
- [Desktop copywriting](evidence/b1-customer-roles-p0/b1-copywriting-desktop-FIXTURE.png), [mobile copywriting](evidence/b1-customer-roles-p0/b1-copywriting-mobile-FIXTURE.png).
- [Platform directory](evidence/b1-customer-roles-p0/b1-management-FIXTURE.png).
- Mobile sections: [members](evidence/b1-customer-roles-p0/b1-settings-members-mobile-FIXTURE.png), [knowledge](evidence/b1-customer-roles-p0/b1-settings-knowledge-mobile-FIXTURE.png), [brand](evidence/b1-customer-roles-p0/b1-settings-enterprise-mobile-FIXTURE.png), [assets](evidence/b1-customer-roles-p0/b1-settings-assets-mobile-FIXTURE.png).

`scripts/b1_local_fixture.sh` stages a temporary Git snapshot preserving bundled Skill modes, uses an existing interpreter, and runs only its named tests. This is not Full pytest, Stage 2, Candidate Build or release qualification. No services/dependencies are installed. Browser tests intercept business HTTP; no real model quality is claimed.

## Public environment observations and remaining acceptance

- Opening new Test in actual Chrome failed with `net::ERR_INVALID_AUTH_CREDENTIALS` before application login. Authentication was not disabled, injected into URLs, or bypassed. No credentials were printed or included in this candidate. Owner was given the existing local secure handoff locations separately.
- Consequently **new Test desktop/mobile/role login/copywriting/download/expiry browser operation remains NOT_VERIFIED**. The successor is not installed there. Screenshots above are explicitly synthetic Fixture evidence.
- Real PG/Redis/Worker behavior for this successor remains NOT_VERIFIED; the base source's historical acceptance is not promoted to fresh successor acceptance.
- Read-only public GET health checks returned Production HTTP200/status `ok`/environment `production`; old Preview HTTP200/status `preview`/Source `6ab24c29e6eed7963c445304dd880ddbe6e8a42a`. No remote mutation/deployment/restart was attempted.
- Knowledge health and real copywriting content quality in new Test were not probed. No paid calls used to close those gaps.
- Controller must authorize successor integration/deployment and conduct authenticated new Test role/browser acceptance. This does not authorize Production publication.

## Handoff fields

```text
PLATFORM_ONLY_MANAGE=PASS_FIXTURE
ENTERPRISE_WORKSPACE_SETTINGS=PASS_FIXTURE
MEMBER_MANAGEMENT=PASS_FIXTURE
BRAND_AND_KNOWLEDGE=PASS_FIXTURE
CREDIT_USAGE_VIEW=PASS_FIXTURE_REAL_AGGREGATES_NOT_PROVIDER_BALANCE
AGENT_AVAILABILITY_TRUTH=PASS_FIXTURE_RELEASE_AND_CONFIG_ADMISSION
TWO_TENANT_ROLE_ISOLATION=PASS_FIXTURE
DESKTOP_BROWSER=FIXTURE_PASS_PUBLIC_NOT_VERIFIED
MOBILE_BROWSER=FIXTURE_PASS_PUBLIC_NOT_VERIFIED
COPYWRITING_BROWSER=FIXTURE_PASS_PUBLIC_NOT_VERIFIED
COPY_AND_DOWNLOAD=FIXTURE_PASS_PUBLIC_NOT_VERIFIED
ZERO_COST_TESTS=84_PASSED_1_RAW_SKIP_PLUS_117_JS_PASSED_PLUS_BROWSER_FIXTURE
PG_REDIS_WORKER_E2E=NOT_VERIFIED_FOR_SUCCESSOR
MIGRATION_REQUIRED=NO
PRODUCTION_UNCHANGED=PASS_NO_REMOTE_WRITES
PREVIEW_UNCHANGED=PASS_NO_REMOTE_WRITES
NEW_PAID_CALLS=0
REMAINING_BLOCKERS=SUCCESSOR_INTEGRATION_DEPLOYMENT_AND_AUTHENTICATED_PUBLIC_BROWSER_ACCEPTANCE
CONTEXT_STATUS=INDEPENDENT_CANDIDATE_ONLY_NO_LIVE_PUBLICATION
```
