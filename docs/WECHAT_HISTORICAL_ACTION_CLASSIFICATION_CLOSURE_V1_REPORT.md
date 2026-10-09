# WeChat Native Historical Action Classification Closure V1

Date: 2026-10-09 (Asia/Shanghai). Customer target: 2026-10-10.

Candidate result: `WECHAT_HISTORICAL_ACTION_FIX_CANDIDATE_READY` (code + isolated regression only).

```text
NATIVE_PRIMARY_STATUS = NOT_REVERIFIED
PRIMARY_INSTALL_AUTHORITY = NOT_GRANTED
REAL_RUNTIME_TEST = NOT_EXECUTED
HISTORICAL_APPROVAL_TEMPORAL_VALIDITY = NOT_PROVEN
```

No complete Native PASS, restart/recovery qualification, new action grant or real model quality acceptance is claimed. The former single-record association restriction is replaced in the Native default route, not bypassed by ignoring failed rows or introducing individual Task Witnesses.

## Frozen Source and formal 06 evidence

- Direct parent Source: `ea9b3cdc53731e7e5098b075eadd8fed2fb44d1b`.
- Parent tree: `bf5a0fbfea69ad5cbd5d4232257d026ce75332e1`.
- Branch: `codex/wechat-historical-action-classification-v1`.
- New `HISTORICAL_ACTION_FIX_SOURCE` / `HISTORICAL_ACTION_FIX_TREE`: final fresh Git publication receipt; no self-referential seal is embedded here.
- PRIMARY Application remains `d8a7814f1ceb528cad8a2d3b4c7a5aa135f6ccf7`, tree `6021a1f5017b92c3e5b785db1dd6bfcbb190d7d5` (06 attestation, no new probe from this window).

Both formal local 06 materials were read completely:

- `WECHAT_HISTORICAL_ACTION_CLASSIFICATION_V1_REPORT.md`, SHA256 `29c42fc3f224440e690152870d8f8b1f4c5748250d2d5755892f7e989bd0d660`.
- `WECHAT_HISTORICAL_ACTION_CLASSIFICATION_V1_AUDIT.json`, SHA256 `1fd7e443b4ada1861ea1f025a22dca7d1f7b7cb7796b829964542008ff5f9500`.

They identify the fresh 2026-10-09 11:53 CST inventory, exact d8a/ea9 identities, readonly collection, 4 Tasks/Runs/contexts, 2 completed PREPARE, 2 CREATE_DRAFT probes denied BEFORE execution, zero unknown/orphan/unmapped/unassociated records and no new Witness. The earlier 06 PREPARE packet is retained byte-for-byte for inherited regression and qualification reconstruction; it is not the new Native default classification source.

## All observed records

| Result class | Task | Run | Historical Source |
| --- | --- | --- | --- |
| HISTORICAL_CONTROLLED_PREPARE_COMPLETED | `18178bf7-c89a-4400-b31e-86cc5c78b97e` | `60b1839a-510e-4455-98a3-789b8f219ccb` | dca |
| HISTORICAL_CREATE_DRAFT_PERMISSION_DENIED | `5144bfc2-2b02-49bd-914d-cd8ecce073bc` | `ae32f5c4-5c26-47e2-9d32-856ee481e14a` | dca |
| HISTORICAL_CONTROLLED_PREPARE_COMPLETED | `ded92a51-7d08-4812-afcd-f35ac3781cf6` | `094eb7e8-f8d4-4bd6-bde5-4c81a421e1fa` | d8a |
| HISTORICAL_CREATE_DRAFT_PERMISSION_DENIED | `175a6d7b-1feb-4d60-8d25-d6b869a205f9` | `89e41e6c-2447-4940-ba04-fb3e27bf3650` | d8a |

The targeted `5144...` failure is not accepted merely because it is failed. Its 06 evidence identifies exact ownership, Agent/Agent Revision/Skill Revision, Approval/Qualification, permission event `BLOCKED_NOT_EXECUTED`, Skill `failure_phase=action_permission`, propagated `SKILL_ACTION_NOT_ALLOWED`, no exit/runtime/workspace/artifact/media/network targets and no workspace. All those boundaries are implemented. The unpersisted underlying domain error and absent independent packet telemetry remain unknown; current real WeChat configuration is never used to infer either.

## Five static conflicts — complete repair map

| ea9 conflict | Minimal replacement | Preserved boundary |
| --- | --- | --- |
| Single Task/Run/Context and event/packet identity | `HistoricalActions.record/anchor/metadata/verify`: shared bounded inventory plus the TWO existing aggregate acceptance documents | No Task IDs hard-coded in classifier; original association/hashes mandatory; new/unanchored/reused record rejects |
| Completed-only terminal rule | Separate PREPARE-completed and permission-denied state machines in `metadata` | Failed alone is insufficient; execution/normalization/runtime/exit/workspace/artifact evidence rejects |
| action=request_action=PREPARE; negative_probe=false | Qualification action remains PREPARE; request action/negative flag checked separately for each supported type | Unknown actions and mixed PREPARE/CREATE_DRAFT evidence reject; no CREATE_DRAFT success authorization |
| Historical Source must be current d8a | Exact archived Approval + audited finite historical Source/tree/code identities | dca remains historical, never current Application identity; new Candidate Source checked independently by Native entry |
| PREPARE marker and successful disk Receipt always required | Exact per-TYPE formal marker; PREPARE disk/execution receipt vs CREATE_DRAFT database failure receipt/propagation | No manufactured denied disk Receipt; absent denied workspace is verified; failed child execution is not relabeled |

`wechat_runtime_native_successor.py` routes qualified historical contexts to the new classifier by default. Ordinary Chat and formal Runtime Test branches are unchanged. Recognized history remains **unchanged** in the delegated parent view. All Config/Terminal Witness/Receipt V2/seeding/unrelated protected table checks remain mandatory.

The explicit legacy adapter parameter on the pure snapshot function remains only for the inherited isolated PREPARE replay tests. The actual Native CLI provides no such selector and now loads `HistoricalActions.load()`; it cannot select the old single-record fixture through a request or operator option. No old seal, file or class was overwritten to simulate compatibility.

## Provenance and integrity implementation

`scripts/wechat_historical_actions.py` reads the exact 06 bounded inventory from one fixed protected evidence path, not an active permission registry. It derives associations from the existing accepted records and shared origin artifacts, never from action/status text or a new per-Task Witness.

Common rules cover exact Tenant/owner/Agent/Agent Revision/Skill Revision/package/fingerprint, unique Task/context/Run association, exact event set/ownership/timestamps, qualified disabled-model policy, observed qualification canonical/sorted-JSON identities, Source-bound controlled/dispatch provenance and zero external-call metadata. There are only two supported historical types; PREPARE failure, executed CREATE_DRAFT failure, unknown action/context and integrity anomalies stay fail closed.

Strict Native verification adds original full Task/Run/Context/tool-policy hashes, every raw/canonical audit hash, persisted Run/control/result joins, archived Approval validation via the existing contract and shared qualification `Authority.receipt()`, existing aggregate acceptance original-byte hashes and exact terminal associations, original historical code SHA pins, formal markers, original successful disk receipt or complete denied database receipt/empty-workspace proof. It never loads the current secret or infers historic failure from connected status.

For denied records, all propagated errors must be `SKILL_ACTION_NOT_ALLOWED`; permission blocked/no network/media, action_permission phase, null exit/runtime/workspace, empty artifacts, failed Task/Run/control result, matching Skill Receipt and controlled-failure refs are independent checks. Root reports are not substituted for those checks.

Both output types have `execution_authorized=false`, `publish_eligible=false`, temporal validity NOT_PROVEN. Historical recognition cannot satisfy ordinary Chat, formal quality PASS, Publish/Enable, new PREPARE/CREATE_DRAFT, credential resolution or Admin Grant. Existing active Action Permission continues to control new Skill execution. CREATE_DRAFT registration remains False.

## Existing aggregate anchors — no new Witness

Native verification reuses these unchanged original artifacts:

1. `/opt/enterprise-agent-workbench-test/release-evidence/wechat-dca-successor-v1-20261007/final-live-attestation.v1.json`, SHA `ceafec2a840493357439e2491dc03656fcef4fe011b2a69d23349d3b9dc905ea`.
2. `/opt/enterprise-agent-workbench-test/release-evidence/wechat-d8a-seeding-approval-v1/live-prepare-continuation.v2.json`, SHA `374278866f7165a5293dc98debaa289b9cccfc96c49cc0d858522953245828b6`.

The inventory's per-record original observations are fixed evidence, not a mutable approval list. Code uses generic type/association rules and validates original aggregate corroboration at Native time. There is no witness minting, per-Task file generator, whole-table exemption or silent new-history enrollment.

## Fresh regression

| Suite | PASS | failed / errors / raw skips |
| --- | ---: | --- |
| New all-four-record/five-conflict/classification negatives | 36 | 0 / 0 / 0 |
| Inherited exact PREPARE fixture replay | 21 | 0 / 0 / 0 |
| Exact Admin / Native component lifecycle | 28 | 0 / 0 / 0 |
| Runtime Test / quality / Productization | 52 | 0 / 0 / 0 |
| Persistent Config | 42 | 0 / 0 / 0 |
| Secret/API/MCP | 60 | 0 / 0 / 0 |
| Personal Config | 23 | 0 / 0 / 0 |
| Receipt V2 / canonicalization / seeding / cleanup | 95 | 0 / 0 / 0 |
| V1 seeding | 30 | 0 / 0 / 0 |
| Total | 387 | 0 / 0 / 0 |

All 351 inherited cases were rerun, not just reused. Four positive cases use the actual 06 records. Qualification objects are reconstructed from the unchanged formal Source contract, earlier complete qualification packet and each record's exact identities; each object's **original observed hash is independently matched**, not replaced. Tests reject wrong Tenant/owner/Agent/Revision/Skill, unknown/mixed Action, forged Receipt/approval identity, missing/duplicate audit, new/running/reused mapping, runtime/exit/workspace/artifact/network/media side effects and execution-phase failures. Current revoked authorization still rejects new execution; ordinary unqualified Chat, no-evidence/failed Publish, formal PASS/FAIL/Admin/Config/Terminal/Receipt/seeding retain their inherited gates.

The all-four Native classification replay explicitly substitutes other prerequisite checks and original verification with metadata semantics to test routing and unchanged parent-view history together. **It is not the full `NativeSuccessor.verify()` entry or a PRIMARY status PASS.** Strict native verification is never changed to accept redacted hashes; tests verify redacted records cannot pass the original-hash path. No source/canonical hash is mocked to claim complete Native success. The 351 historical tests retain their documented isolated DB/Runtime/native-authority emulation limitations. No Full pytest, Stage 2, Candidate Build, WSL or real Provider execution occurred.

Offline reproduction from the exact checkout's `enterprise_agent_poc`:

```sh
PYTHONDONTWRITEBYTECODE=1 python -B scripts/verify_wechat_historical_actions.py \
  --audit /absolute/readonly/WECHAT_HISTORICAL_ACTION_CLASSIFICATION_V1_AUDIT.json \
  --prepare-fixture /absolute/readonly/WECHAT_PREPARE_AUTH_READONLY_FIXTURE_V1.json
```

## 06 Native handoff / original-only checks

No Root installation or write authority is supplied by this report. Under separate approved readonly preparation:

1. Fresh transfer/verify the NEW Candidate Source/tree with direct parent ea9. Bind fresh Scope/Approval/policy/complete file map to the new Candidate. Retain publication_authorized=false, zero external-call environment/budget, exact Test DB/Redis and required-gate settings. Never reuse old Candidate identity or revoked/expired scope.
2. Native approval code map now includes exactly FIVE files: `scripts/wechat_runtime_native_successor.py`, `scripts/wechat_runtime_test_lifecycle_guard.py`, `scripts/receipt_row_canonicalization.py`, `scripts/wechat_historical_prepare.py`, `scripts/wechat_historical_actions.py`. Policy shape, historical protected pins and transitive parent identity requirements are unchanged.
3. Fixed new evidence lookup: `/etc/enterprise-agent-test-exact-admin-v1/historical-action.audit.v1.json`, byte-identical to the 06 inventory SHA above, root:root/read-only/no symlink/trusted ancestors. It is evidence-only; no new approved Task record or authority is created. This window did not install it. The old PREPARE packet/evidence remains immutable and is not the new default Native lookup.
4. Use current protected reader privileges or explicitly approved byte-identical staging for the original two aggregate reports, archived approvals and nonsensitive receipts. The optional fixed staging location is `/etc/enterprise-agent-test-exact-admin-v1/historical-action-originals/<ORIGINAL_SHA256>.json`, root:root/0444 with trusted ancestors. The verifier checks original SHA before parsing; bad staged content rejects without fallback. Do not chmod old dca evidence, copy redacted reconstruction under an original identity, invoke unapproved sudo or rewrite files. No staging/copy was performed here.
5. Archived Approval paths and hashes come from the inventory; original historical Git objects/code hashes must be available in the exact read-only Git checkout. The successful receipts use their real runtime-profile workspace paths. Denied workspace must remain absent, with no symlink/unsafe existing ancestor. Source profiles/rows, actual file modes, read privileges, original complete payloads, result/Receipt propagation and canonical collector representation must be checked on PRIMARY internally; the export cannot rehash omitted fields.
6. Perform ONE complete actual read-only Native status covering all four histories plus other current data. Preserve source integrity, schema/table identity, Config/Terminal Witness, Receipt V2/seeding and transitive parent validation; execute wrong Authority/Source/Tree/Run/Production and history-integrity negatives under isolation. Report the real first remaining error without broadening rules.

The actual Native entry remains:

```sh
# Future 06 approved READONLY context only; not executed on PRIMARY here.
PYTHONDONTWRITEBYTECODE=1 python -B \
  scripts/run_exact_test_admin_lifecycle.py status --run-id "$EXACT_APPROVED_RUN_ID"
```

Run as the established `lucky` UID 1000 with root-controlled exact source/authority paths, explicit permitted Git configuration, original parent roots, Python -B/env=1, disabled-provider Test environment and read-only OS/PG enforcement. `status` writes no audits, permissions, Tasks, services or manifests; it does not call prepare/grant/revoke/recover or a Skill. Do not use Windows semantic PASS as native UID/PG/systemd parity.

## Safe recovery and still-unverified conditions

`SAFE_RECOVERY` is not advanced from 06's NOT_READY by this Candidate. Complete Native positive/negative validation, real read permissions/staging, unchanged current config/Secret-v1 metadata and actual safe startup/restart/recovery must be independently accepted before any install, Grant or Runtime Test. Provider/network/budget/real quality acceptance remain separate and NOT_EXECUTED.

If future authority operations are interrupted, inherited exact Admin cleanup still requires exact run/owned membership and independent quiescence proof; final active admin count must be zero. Do not delete historical actions/quality rows, alter WeChat config/Secret, amend old receipt/seal or start an incompatible old d8a Guard to force recovery. Retained new lifecycle rows still prohibit unreviewed direct old-Guard rollback. PYC stays under prior 06 cleanup/-B policy; no new exemption or cleanup occurred.

Missing underlying historical permission domain error, packet telemetry and Approval effective/expiry/revoke timestamps remain NOT_PROVEN/EVIDENCE_NOT_FOUND. No current-account lookup, file mtime or unrelated Provision Scope is used to fill those gaps.

## Changed files and safety

- `enterprise_agent_poc/scripts/wechat_runtime_native_successor.py`: default generic classification route, output separation, code pin set.
- `enterprise_agent_poc/scripts/wechat_historical_actions.py`: limited historical types and exact evidence/provenance checks.
- `enterprise_agent_poc/scripts/verify_wechat_historical_actions.py`: directed offline entry.
- `enterprise_agent_poc/tests/test_wechat_historical_actions.py`: 36 directed cases.
- This report.

Application/Skill/Secret/UI/Provider/formal Runtime quality/schema/Production code unchanged. Original d8a, historical records/receipts/approvals, two aggregate anchors and all original exports remain unchanged. The old uncommitted pending audit and main workspace unrelated dirty content are excluded from this commit.

`PRIMARY Changes=0` · `Production Changes=0` · `PRIMARY SSH attempts=0` · `real Admin/Runtime Test=0` · `Provider/WeChat/Image Calls=0`.

STOP after exact Candidate Commit/Push/fresh verification. No PRIMARY install or real execution.
