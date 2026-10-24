# Standard Test isolated candidate context V1

This is a local implementation candidate, not an installed or authorized Test
executor. Production, customer Preview and the894 PRIMARY stay unchanged.
The historical d8a→894 executor and its records are not loaded or modified.

Business Source is an input. The Tooling Source is the commit containing this
document. Use its exact Source/tree and complete file pins when the existing06
protected material workflow adopts it. No signer or default grant is supplied.

## Executable entries and callback meanings

`scripts/test_delivery_context.py plan-isolated` reuses the existing delivery
plan/build/Manifest validators. It creates an **unsigned** operation request:

```text
python -B scripts/test_delivery_context.py plan-isolated
  --repo <exact-Git-repository> --source <candidate> --tree <candidate-tree>
  --archive <existing-archive> --manifest <existing-manifest>
  --observation <readonly-PRIMARY-observation.json>
  --observation-sha256 <actual-observation-file-sha256>
  --output <new-unsigned-request.json>
```

Only the already-adopted `test_delivery_verified_entry.py` may invoke the new
`execute-isolated --request --policy --grant --archive --manifest` mode as Root.
Use the existing installer/technical execution channel, isolated Python `-I -B`,
and a controller inside `enterprise-agent-test.slice`. Ordinary CLI/SSH/sudo
access does not constitute adoption or technical execution authority. No actual
invocation/installation is approved by this implementation document.

The existing seven-stage `dispatch_delivery` remains unchanged. Its fixed
callbacks are mapped by the new worker `test_delivery_isolated.py`:

| Journal stage | Actual isolated action |
|---|---|
| preflight | Verify request and transfer hashes, capacity, fresh empty target |
| backup | `NOT_APPLICABLE_NEW_EMPTY_INSTANCE`; no previous private data exists. PRIMARY backup is `NOT_PERMITTED` |
| install | Allocate a new marked directory, use existing strict archive extraction, read back exact installed bytes. No PG/Redis process yet |
| activate | `ISOLATED_START_ONLY`: existing private PG/Redis lifecycle, synthetic tenant/user fixtures and existing API/MCP/Worker subprocesses. No current pointer, unit drop-in or historical switch call |
| health | Existing API/MCP/Worker health/CWD checks and private PG identity/version/ledger fingerprint |
| qualify | Existing zero-cost five-turn/auth/tenant/SSE/history/idempotency/cancel/unknown-execution checks. Dump populated private data, restore into a distinct new private database and read it with the SAME candidate |
| export | Exact original artifact identity, installed-byte readback, measured check results and redacted failures. No rebuild or Production authority |

`old_reader_compatibility` is deliberately replaced by `same_source_restore`.
No894 archive is required or fabricated.894 installed identity is independently
observed before execution, after every callback and after cleanup. Its missing
original archive/Manifest remains a future PRIMARY-switch gate only.

## One private lifecycle; one actual global lock; one durable journal

The Root controller holds the existing
`/run/lock/enterprise-agent-test-successor-v1.lock` inode throughout fresh
authority checks, callback effects and the existing hash-chained Journal.
It never creates/replaces that lock. Another process or an inode change rejects.

One existing systemd sandbox owns all private services across the seven phases.
The fixed stdin/stdout bridge passes stage, request hash and previous-receipt
hash only; commands/callbacks cannot come from request JSON. No per-phase SSH
session or detached PG lifecycle is introduced. The controller checks current
authority and lock identity while waiting. EOF/exception stops the one marked
PG/Redis instance; the controller also stops/collects the exact transient Unit.
Success requires exit0, an exact post-cleanup receipt, MainPID0 and inactive
Unit state. A collected/not-found Unit is accepted only with those state fields.

The existing Journal retains claim/intent/result records through failures.
An uncertain intent is not NO_WRITE. Duplicate claims never invoke callbacks or
append a new failure to a previous operation. No automatic replay/recovery is
provided. Failure receipts are bounded safe status/hash/category data and are
written outside disposable PG storage before/after cleanup as appropriate.

Resources reuse `PRIVATE_STAGE2_TMPFS_V2`: private network/devices, host data and
sockets hidden, dedicated non-Root UID distinct from protected service UIDs,
Test slice, CPU100%, memory1280/1536MiB, swap0,900s Unit budget plus cleanup
reserve,704MiB private tmpfs, aggregate768MiB, inputs63MiB, Root receipts1MiB,
host reserve256MiB and disk strictly above20%. Requests are capped at64KiB;
cumulative wire receipts at256KiB leave room for Journal/export envelopes.
No changes to parent slice3/4GiB or PRIMARY/Preview limits are made.

## Existing technical channel: required real inputs, no issuer here

`technical_scope()` composes the existing `delivery_scope()` with the fixed
`ISOLATED_CANDIDATE_CONTEXT_V1`/`ISOLATED_CANDIDATE` target. The policy, grant and
raw technical decision must be Root-protected, hash-pinned, current and match
the exact operation/request/actor/host/Tooling/runtime. Revocation is reread;
the protected operation directory enforces single use. The historical e87 and
all other expired, failed or mismatched records remain unusable.

Required independent technical decision fields consumed from the existing
actual06 channel are:

* kind `ACTUAL_OWNER_AUTHORIZED_06_ROOT_DECISION_RECORD`;
* technical_action `execute_standard_test_isolated_candidate_v1`;
* context/target above, actual_root_uid0, actual Owner references and actor/host;
* request_sha256 and policy_scope_sha256;
* issued_at/expires_at integers, at most4h, uses1, revoked=false;
* provider_budget0, Production/Preview/PRIMARY-switch authority allfalse.

`policy_scope_sha256` hashes every policy field except the single
`accepted_issuer_record_sha256` back-reference. This avoids an impossible
self-hash cycle while binding paths/operation store and all permissions.
The protected policy pins the actual decision's original file hash; the grant
pins the full final policy hash. Neither strings in chat nor the test fixtures
create these real records. This document does not assert an issuance occurred.

Actual independent confirmation, authentication and protected file installation
remain the responsibility of the existing06 channel. It must adopt this exact
new fixed Test action; an old source-bound CLI or ordinary sudo cannot do so.
Do not request a short execution window before the following dependencies exist.

## Trusted material and runtime gates

Three separate protected roots are required below
`/opt/enterprise-agent-test-delivery`: Tooling, reusable Runtime and operations.
No path uses894 current/shared as a writable install destination. Root/source
Git repositories must be self-contained, protected and have no alternates or
external commondir. Source/tree/parents and actual archive bytes are rechecked.

The existing `test_delivery_adoption.finalize` supports the explicit isolated
context with candidate-only artifact adoption. The legacy candidate+predecessor
branch remains unchanged. Its complete Git blob/owner/mode/ACL/runtime checks
and true Root decision checks remain mandatory. Material adoption is neither
execution authority nor Native PASS.

Runtime inventory is not dependency closure. The consumer additionally requires:

* complete protected `runtime_inventory()` digest including explicit link edges;
* a pinned real runtime closure receipt measuring dynamic_loader, python_imports,
  pgvector_load, redis_start and relocation as PASS for the actual non-Root
  operator, Runtime root/Python hash and exact sandbox profile digest;
* actual material-adoption receipt for this context/Tooling;
* an independently measured namespace/no-host-socket/write-containment/disk-full/
  cgroup-termination/zero-egress/proc-isolation receipt tied to this driver hash.

These are consumers of real measured receipts, not receipt generators. Missing
inputs reject before claim/start. Existing mutable user-owned Runtime may be
used in explicitly labelled non-Root component tests, never as Root executable
trust. A once-only qualified closure can be reused across compatible Sources;
no per-Source Runtime copies or new Runtime manager are added.

## Validation levels and remaining handoff

Focused tests cover fixed callbacks, replay/authority/target rejection, actual
pipes/EOF, actual Linux locks, private Journal fsync, Source reuse and byte
tampering. Synthetic authority/Unit doubles are explicitly labelled and are
never Root-protected signed input. A separate actual PG16.6/vector0.8.6/Redis
component control test initializes only a new private synthetic database and
then stops/removes it; it starts no candidate application.

Two exact Sources use the SAME helpers to verify and install existing artifacts:
B1 `801fd27a92f32ecc87e9aec1a8c253a8942d1e92` and
`894bdd2a031725a0d0df3b95d6dfc26d08f0e4ab`. The latter package is labelled
RECONSTRUCTED_FOR_SOURCE_REUSE_ONLY, not the original894 release or rollback.

Neither component success nor prior B1 DEV integration is TEST_NATIVE_PASS.
Current remaining real gates: protected Runtime/Git/Tooling material adoption,
independent non-Root operator and sandbox qualification, exact valid technical
record/grant, then one authorized B1 isolated run. No Push/install/deployment is
part of this local implementation. Production and PRIMARY switch stay denied.
