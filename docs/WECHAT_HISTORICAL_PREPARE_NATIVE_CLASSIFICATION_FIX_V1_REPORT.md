# WeChat Historical PREPARE Native Classification Fix V1

Date: 2026-10-09 (Asia/Shanghai). Customer target: 2026-10-10.

Status: `WECHAT_NATIVE_PREPARE_AUTH_FIX_BLOCKED`.
Unique FIRST_FAILURE_POINT: `STRICT_NATIVE_STATUS_REPLAY_NOT_PROVEN_FROM_REDACTED_FIXTURE`.

The source classification fix and directed metadata replay are implemented and passing. Complete strict Native status cannot be certified from the supplied redacted packet: it explicitly declares `complete_native_snapshot=false` and omits the original Context/Policy/Audit bytes needed to recompute their original full hashes. Those strict checks remain present and the redacted input deliberately fails closed. No positive complete Native status is claimed. This normal-pushed Source is a **review Candidate**, not an installable/qualified Guard.

## Identity and immutable evidence

- Direct parent Candidate: `30981091b098b582b4471a894c0f1d191fd7fc65`, tree `cfbf3c963c69da226b84b854073d876d4a2f5adf`.
- Branch: `codex/wechat-historical-prepare-classification-v1`.
- New `PREPARE_GUARD_FIX_SOURCE` / `PREPARE_GUARD_FIX_TREE`: final publication receipt. Do not use 309 as the repaired Source.
- PRIMARY remains Application `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`, tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5` (06 evidence; no new server probe here).
- Read the complete `WECHAT_PREPARE_AUTH_READONLY_FIXTURE_V1.md` and `.json` supplied by 06, including their redaction/time/provenance limitations.
- Markdown SHA256: `6a0b9e1284af560fc5298923c783305d880be461da49b68e01ea123865836688`.
- JSON byte SHA256: `9b1eaa6ce86c9b0221eb2da36ce3566fad97e964f0887092717bc0c2159f2daf`.

Exact first rejecting object reconstructed by 06's actual collector ordering and joins:

| Item | Identity |
| --- | --- |
| Task | `ded92a51-7d08-4812-afcd-f35ac3781cf6` |
| Run | `094eb7e8-f8d4-4bd6-bde5-4c81a421e1fa` |
| Context | `1214b292-31a5-4cb0-8bb3-b2774abd4f54` |
| Conversation | `2387b785-f81d-4da1-b96c-4142d111bc19` |
| Tenant | `internal-test-staging-v1` |
| Actor | `79848512-1a83-49ae-84b2-c2868d0e5ad3` |
| Agent / Revision | `42bce66b-6a00-4e11-ad02-bd7325bb3edb` / `93e5f01c-5a7e-4472-a7e7-92ee83e327d5` |
| Skill / published Revision | `828d61cf-56ce-452a-a179-eac4264dcb76` / `e981c9f1-9232-4772-90e0-703dd84a265b` |
| Invocation | `e5cef9a6-d373-4380-8058-25e5f50259e9` |
| Action / result | PREPARE / completed Task, Run, Skill execution / exit 0 |
| Execution | 2026-10-08 23:32:38–23:32:40 Asia/Shanghai |

Original Native error JSON did not record these IDs; their exact association is a later deterministic readonly reconstruction, not an ID quoted from the old error log. Older 10-07 smoke IDs were not substituted.

## Root cause and minimal fix

Frozen 309's `validate_snapshot()` classified `runtime_test=true` as formal testing and every other unpinned context as ordinary Chat. The exact context is actually `SKILL_ONLY_TEST_QUALIFIED`, `runtime_test=false`, `model_execution_disabled=true`. Its instance remains configured and real Runtime quality rows are zero. The old branch therefore retroactively required a future Enabled/quality PASS state and absent eligibility mode for a completed controlled history record.

The new branch recognizes qualified history through `scripts/wechat_historical_prepare.py`, not by action name alone. It consumes only the exact independently supplied 06 evidence packet; no wildcard Tenant/User/Task, caller-chosen artifact path, active permission registry or arbitrary historical approval is introduced.

Native recognition requires exact Task/Run/Context/Tenant/Actor/Agent/Agent Revision/Skill Revision/fingerprint, terminal completed state, unchanged timestamps and mappings, exact disabled-model Tool Policy and Qualification Receipt identities, permission/Skill execution/controlled-action event identities and metadata. **Strict native verification additionally requires the original full Context/Policy hashes, raw/canonical audit hashes, Run's persisted controlled audit hash, exact immutable versioned d8a approval file, its existing formal contract validation, formal controlled Task marker and exact disk receipt hash.** File reads use trusted ancestors/expected owner/mode, no-follow/inode checks and bounded nonsensitive JSON; no article or secret file is read.

The historical context, mappings and audit rows remain unchanged in the delegated parent view. No history is projected out as an exemption. Existing Config/Terminal/Receipt V2/seeding/other protected data checks still execute and may independently reject. Parent seals and witnesses are not repinned.

Classification boundaries:

- A: exact completed historical Controlled PREPARE -> evidence recognition only, `execution_authorized=false`, `publish_eligible=false`.
- B: ordinary Chat -> unchanged Enabled + real Runtime PASS + exact ordinary-principal permission; absence of controlled eligibility remains mandatory.
- C: formal Runtime Test -> unchanged exact Admin admission, A/B/C evidence checks, PASS/FAIL persistence and independent publication eligibility.
- D: new Skill Action -> historical recognition supplies **no grant**. Existing controlled entry/current active Action Permission remains mandatory. New/running Task, reused history mapping, unknown Context or another qualified action fails this historical branch.

CREATE_DRAFT registration remains False. Its declared scope string inside the historical policy is not execution authority.

## Evidence and time boundary

```text
HISTORICAL_PREPARE_EVIDENCE = VERIFIED
HISTORICAL_APPROVAL_TEMPORAL_VALIDITY = NOT_PROVEN
NEW_EXECUTION_AUTHORITY = NONE
FORMAL_MODEL_QUALITY_EVIDENCE = NONE
```

The 06 evidence proves hash-linked historical provenance: Qualification, Approval identity, exact persistent execution/audit associations and disk Receipt match. Local replay independently recomputes the fully provided Qualification Receipt canonical SHA and formal sorted-JSON audit identity. It cannot independently rehash redacted originals; their original observed identities remain protected strict native comparison targets, not hashes newly generated from sanitized current data.

The actual PREPARE Approval contract has no issued/effective/expiry/revoke fields or temporal lease check. Therefore non-authorizing history classification does not invent such a requirement or a time PASS. Filesystem mtimes do not prove activation/expiry/revocation. The separately revoked Minimal Provision V2 Scope concerns a different Synthetic Tenant and is explicitly **not** authority for this PREPARE. No Provision revocation timestamp is used as a PREPARE lease boundary.

## Fresh regression results

| Suite | PASS | failed / errors / raw skips |
| --- | ---: | --- |
| Exact 06 history metadata/classification directed tests | 21 | 0 / 0 / 0 |
| Inherited exact Admin/native-component lifecycle | 28 | 0 / 0 / 0 |
| Inherited Runtime Test/quality/productization | 52 | 0 / 0 / 0 |
| Persistent Config | 42 | 0 / 0 / 0 |
| Secret/API/MCP | 60 | 0 / 0 / 0 |
| Personal Config | 23 | 0 / 0 / 0 |
| Receipt V2/canonicalization/seeding/cleanup | 95 | 0 / 0 / 0 |
| V1 seeding | 30 | 0 / 0 / 0 |
| Total | 351 | 0 / 0 / 0 |

Directed tests reuse the actual packet, exact IDs/times/qualification/policy/audit metadata—not a different simplified positive PREPARE. They reproduce frozen 309's exact ordinary-authorization failure by its AST and verify the new metadata classification preserves historical parent-view rows and read-only inputs. Wrong Tenant/Actor/Agent/Revision, missing/forged qualification/audit/Receipt, new/running Task, extra mapping, revoked active authorization, unknown action, unpublished Chat and CREATE_DRAFT all reject. Temporal validity remains NOT_PROVEN.

**Directed tests 19–21 explicitly emulate other prerequisite branches and replace full-row verification with the metadata-only method for classification replay. This is not complete `NativeSuccessor.verify()` qualification. Test 16 asserts the unmodified strict verifier rejects the redacted original-hash input. No hash is mocked to claim a native PASS.** The existing 330 cases likewise retain their documented isolated DB/Runtime/Root emulation limits; no real Provider PASS or PRIMARY restart is inferred.

The initial directed harness lacked a table key in its explicit prerequisite emulation (3 errors); it was repaired and all 21 tests rerun successfully. Final failures/errors/skips are not hidden. The existing Starlette/httpx warning is non-fatal; dependencies were not changed.

Reproduce the directed offline verifier from the exact checkout's `enterprise_agent_poc` directory:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B scripts/verify_wechat_historical_prepare.py \
  --fixture /absolute/readonly/WECHAT_PREPARE_AUTH_READONLY_FIXTURE_V1.json
```

## 06 Authority compatibility and next native verification

This report grants no installation/write permission. After separate approved readonly authority preparation:

1. Transfer and fresh verify the **new** Candidate Source/tree, not 309; preserve current d8a Application/services and all old evidence.
2. Bind fresh exact Scope/approval/installed-source map to the new Tooling/Application successor identity as in the inherited Candidate contract. Keep publication_authorized=false and read-only OS/PG enforcement for status. Do not reuse revoked/expired Scope or old code hashes.
3. Native approval code map now contains exactly four files: `scripts/wechat_runtime_native_successor.py`, `scripts/wechat_runtime_test_lifecycle_guard.py`, `scripts/receipt_row_canonicalization.py`, **`scripts/wechat_historical_prepare.py`**. Original policy keys and parent pins are unchanged.
4. Independently approve a byte-identical readonly copy of the **existing 06 packet** at `/etc/enterprise-agent-test-exact-admin-v1/historical-prepare.fixture.v1.json`, root:root, non-writable file/ancestors and no symlinks; exact SHA above. This is evidence-only, not a new mutable per-Task witness or re-sealing current data. This window did not place any Root file.
5. Keep original versioned d8a Controlled Approval and exact disk receipt at their existing packet-recorded paths/identities. The verifier reads the versioned immutable approval, not an active Source-switched lookup, and never reactivates it. Missing/changed original artifacts fail closed.
6. Run actual complete Native status using the **original full** consistent readonly snapshot and current transitive parent files. The packet reports four historical contexts but supplies only the exact first rejected object. Other contexts must independently satisfy current rules; never add blanket exemptions to make the aggregate status pass. Report the first actual remaining error if any.

Actual unchanged full native operator entry:

```sh
# Future 06 READONLY verification only, with independently approved exact
# environment/Source/Scope/Root evidence. Not executed against PRIMARY here.
PYTHONDONTWRITEBYTECODE=1 python -B \
  scripts/run_exact_test_admin_lifecycle.py status --run-id "$EXACT_APPROVED_RUN_ID"
```

Use `lucky` UID 1000 and the inherited exact Test PostgreSQL/Redis, disabled-Provider baseline and required-gate environment. Retain read-only transaction/sandbox, no credentials printed, Python -B/env=1. `status` does not call prepare/grant/revoke/recover, issue audit writes, execute a Task or switch a service. Wrong Authority/Run/Production negatives must also pass rejection before any qualification claim.

Safe startup/restart/recovery remains unqualified until complete native positive/negative validation succeeds. No real Grant/Runtime Test/Publish/Enable can start here. Old d8a direct rollback after new lifecycle rows remain persisted retains the inherited compatible-recovery requirement; do not delete records/config or edit seals to force restart. Prior PYC cleanup remains CLEARED per 06; no repeat cleanup or bytecode exemption was added.

## Changed files and safety

- `enterprise_agent_poc/scripts/wechat_runtime_native_successor.py`
- `enterprise_agent_poc/scripts/wechat_historical_prepare.py`
- `enterprise_agent_poc/scripts/verify_wechat_historical_prepare.py`
- `enterprise_agent_poc/tests/test_wechat_historical_prepare.py`
- This report.

No Application d8a code, Skill, Secret, UI, schema/migration, old Guard, historical Task/Run/Receipt/Approval/Witness or Production tooling changed. The prior uncommitted pending audit report and main workspace dirty files are preserved and excluded from this commit. Exact provided Fixture bytes remain unchanged; no raw business/secret content was reconstructed or committed.

`PRIMARY Changes=0` · `PRIMARY SSH attempts=0` · `Production Changes=0` · `real Admin/Runtime Test=0` · `Provider/WeChat/Image Calls=0`.

STOP after review Candidate publication. Complete Native status remains for 06 readonly revalidation; no install authority is granted.
