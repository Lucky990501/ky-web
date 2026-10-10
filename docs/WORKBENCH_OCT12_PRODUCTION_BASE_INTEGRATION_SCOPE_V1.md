# October 12 Production-based integration preparation

Scope: `ISOLATED_PRODUCTION_BASED_INTEGRATION_PREPARATION_APPROVED`.
This code candidate is **not** Release Ready and grants no Production or
PRIMARY deployment, Productization, Runtime Test, or Provider authority.

## Source lineage

The first parent is the exact accepted Production source
`562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3`, tree
`e5205965e57fce6ce2b65dd747a110d00f7f1b1a`.
The reviewed Application increment is
`28e061a114118a27609330125c756e193d3248a6`, tree
`8ff5e4a8ed26c32ba040b4fa27cf1d380f3c7541`.
Three-way integration retains both histories; it is not replacement by the
Application tip. The ten reviewed commits are:

- e7e96b1a959d8631dc9dcd5c24939fa483ac134a — revision-bound Skill runtime.
- b0e96dfd6dfa3d3b71f5b27cb4229eb4905f6092 — Task-scoped Skill dispatch.
- 0bfded6ee6837e8c0908721d8ffab1035506e133 — TEST-only controlled action.
- 4ca21336bdd17a507d819457c2fc41c5e410846b — TEST-only qualification.
- dca318de578a9b9601ad036e1b176bc5ab702029 — canonical Revision identity.
- cb17abb6b2e2fea888c1b5603f169a99a235191a — tenant Secret / connected gate.
- db8e23658baa6e4b707380e178aded561d3280f2 — personal WeChat settings.
- 9e6daef89a0aa6bd10bb8788fbc73207d11dccd5 — synthetic seeding protection.
- d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7 — canonical V2 receipts.
- 28e061a114118a27609330125c756e193d3248a6 — atomic Runtime Test init.

## Preserved Production contracts

Retain task-bound image upload/edit, actual format and OSS storage, safe HTTP
diagnostics, reference retry dependency proof and multi-group terminal state.
MCP image success/failure metadata and Runtime `attempt_observation`'s
`server`/`tool` arguments remain intact. Ordinary tasks do not acquire the
TEST-only controlled-action or qualification privilege.

All PostgreSQL migrations, current Schema015, runtime-only deployment and
post-commit recovery implementations/declarations remain byte-identical to
Production. Schema is 001–015, target 001–015, Migration NONE, Data Contract
`member_account_status_v1`. Historical schema floors and pins are not rewritten.

## Deferred release integration points

| Dependency | Integration point | Required owner / evidence |
|---|---|---|
| Approval Selector successor | external Tooling / runtime pair / exact admin gate | 01, sealed new Tooling Source/tree and loader parity |
| New Application approval | new Source/tree-specific Test contract and Production release declaration | Total Control / 02; old 28e/d8a/77e5 seals are not transferable |
| Production exact predecessor | future versioned runtime-only / recovery declaration | 02 after Tooling delivery; exact current Production562 immutable identity |
| WeChat preview / actual CREATE_DRAFT | frontend and explicit approved action / permission contract | 01 / 03; CREATE_DRAFT remains execution-disabled |
| Existing published Revision upgrade | versioned release Productization transaction | 01 / 02; preserve current pointer/enable/bindings and prove restore |
| New copywriting Skill | independently frozen package / binding / quality qualification | responsible Skill window; e506 is not included |
| Production Skill runtime/artifact installation | formal build inventory plus revision interpreter/lock and Secret contract | 01 / 02 / 06; Test-only managed roots/seals are not Production installation |
| Provider / WeChat / Image acceptance | final Source-bound real task budgets and business matrix | Total Control / 03; this preparation has zero real calls |

The current formal release declarations still belong to the historical562
release and its historical519 predecessor. They are intentionally **not** new
approval for this integration. A future Release must pin
`20261007-562f201-reference-dependency-retry-v1` as its ONE EXACT predecessor
after fresh attestation, never reuse the old519 predecessor.

The old build allowlist does not package the new `integrations/` and
`skill_sources/` assets. Application compilation/component packaging is not
proof of a deployable Production Skill artifact. Formal immutable Candidate
build/preflight and cross-version recovery remain separate pending gates.

## Evidence limits

Preparation tests use isolated Linux fixtures and no live Provider credentials.
Component tests, simulated HTTP/OSS, SQLite/isolated PG and synthetic Native
authority fixtures are not Production Revision Runtime Test, real WeChat draft,
or customer acceptance. Existing PRIMARY current/services and Production state
are not changed. Final Source/tree and remote identity are reported externally
after the integration tests and normal commit/push.
