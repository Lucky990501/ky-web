# V3 producer/consumer state contract — isolated review successor

This section supersedes the historical partial-result sections below, which are
retained as evidence. The direct parent is
`664594ae7c680d5eeadd87e7ec79b103fa1b695c`, tree
`35a727593c5478fc2141f597a8113cd63203d287`. The final Source/Tree is the Git commit
containing this document and is recorded in the separate exact-identity handoff.
No real approval, record seal, Root operation, PRIMARY maintenance, release,
database operation, service operation or external model call is authorized here.

## Version and identity boundaries

The existing `schema015_successor_binding.py` remains the single qualification
consumer. Its new manifest `runtime_only_release.schema_version=4` uses
`SCHEMA015_P0_CONTROLLED_RELEASE_V3_STATE_V1`. Authority records use schema 2
with that exact `state_contract`. Evidence requirements, exact 562 predecessor,
Schema015 and the 280a business base are unchanged. V2 and historical V3 retain
their original exact contracts. New records cannot be parsed as old V3 or V2:
their version, exact field sets, contract, binding hash and successor pins differ.
Old readers reject the new manifest; they must not be deployed under a relabelled
old declaration. Tests execute the actual Git-frozen 7dc readers to prove this.

`release_manifest.py` accepts and validates only the new exact binding, including
the required consumer files in the artifact. Existing release preflight,
`rollback_preflight.verify` and `release_runtime_recovery` all reach the same
qualification function. `schema_015_runtime_only.py` and `release_switch.sh` need
no behavioral change. Recovery preserves the specific state rejection code;
unrelated I/O failures still receive the existing safe generic projection.

The producer and consumer are a coordinated Source/Tree, but **Bootstrap Tooling
identity and deployment Application artifact identity are separate records and
inventories**, even when both refer to this successor Git commit. This is not
the frozen 7dc Application archive. Its old hashes, 147-file/27-directory count,
installer scope and old QA conclusions are not valid for this successor.

## Publication, uncertainty and revocation

Under the canonical Global Release Lock the producer writes its immutable
technical decision, consumed audit/decision markers, intent, exact authorized
outputs, then a `PREPARED` completion. This is deliberately NOT consumer authority.
A missing or uncertain intent/completion/reconciliation receipt rejects.

The existing verified producer entry gains bounded `--reconcile` (mutually
exclusive with `--seal`). It can only reconcile a specified existing operation,
using the original independently approved technical hash, same producer,
candidate, host/operator, input hashes, original audit bytes and a still-valid
technical authorization. It never rewrites an approval, issues a replacement,
resets consumption, or reconstructs missing authority/audit outputs. After
independent read/fsync/read it may finish the exact missing PREPARED completion
and append an immutable durability receipt. Exact repeated reconciliation is
idempotent; changed, revoked, expired or incompletely audited operations reject.

The consumer requires the receipt and verifies its bound original intent,
completion, authority records, technical-decision snapshot and all decision
markers. It checks the technical authorization at the original observation
time; current Owner/deployment validity is separately checked by the unchanged
qualification rules. `PENDING`, `UNKNOWN`, `COMMIT_UNKNOWN`, `REVOKING`, revoked,
missing/unknown states and malformed versions are never authorization states.

Receipt visibility alone cannot prove the receipt writer's final fsync. Thus
the consumer itself performs an independent protected read/fsync/read of the
receipt AND bound original bytes while holding the same lock. This is bounded
durability coordination, not an approval writer or a zero-I/O assertion. A
persistent I/O/readback failure rejects; after independent successful coordination
existing exact bytes may be used without reissuing or reconsuming any decision.
Any producer write error preserves `COMMIT_UNKNOWN`, operation and attempted
phase, with `writes=MAY_HAVE_COMMITTED`; it is never reported as no mutation.

Revocation's protected namespace is the first permanent rejection barrier.
It is checked before Policy movement, after interruption, after exact retry and
before every use. A revoke intent or completed revoked record also rejects.
Neither reconciliation nor repeated revocation can reactivate the original
Policy. No pending namespace, approval, audit or evidence is deleted to recover.

## Actual lock and recovery boundary

The existing release shell holds FD 9 across preflight, switch, verification and
its protected recovery path. V4 requires that descriptor to reference the exact
Root-protected canonical `/run/lock/enterprise-agent-workbench-release.lock`,
proves exclusive `flock` ownership and exclusion of a separately opened contender,
and never releases it on return. Alternate lock paths do not qualify. Producer
operations acquire the same lock and retain it through writes/reconciliation.

Actual separate-process tests prove both orders: a consumer holding the lease
prevents a concurrent revoke; a revoke barrier committed first prevents a later
consumer even when Policy bytes have not yet moved. Merely finding a lock file
is insufficient. These tests use ordinary UID private files and real flock;
Root metadata is explicitly a fixture boundary.

Validation rejection itself performs no service stop, predecessor removal or
pointer change. **The existing approval/evidence expiry rules are not extended.**
If an operation starts but its authority expires before protected recovery, that
recovery remains fail-closed. A separately approved real execution must have a
valid recovery window covering the planned switch and failure handling; this
candidate does not create emergency authority or guarantee recovery past expiry.

## First execution bytes and packaging requirements

`V3_VERIFIED_SOURCE_ONLY_ENTRY_V2` uses byte-manifest schema 2, exact parent 664,
the new Application artifact identity and binding hash. Before Python starts,
an authorized operator must verify the entry's independent expected hash using
an already trusted system tool. The entry then checks the protected manifest,
Git Source/Tree/blob bytes, paths/permissions and the unchanged original nine
input names before compiling captured bytes. Seven are executable modules;
`app/settings.py` and rollback JSON are data only. The entry has its own separate
initial hash. Extra initializers, pyc files, imports and unsafe ancestors reject
before candidate side effects. Candidate code still cannot self-authorize.

For a later separately authorized packaging/installation review:

1. Use this exact successor commit (direct parent 664); do not reuse 7dc/59d/664
   archive or binding identities. Run existing binding `prepare` with
   `--qualification-mode p0-controlled-v3-state-v1`; it derives all seven consumer
   pins from Git and requires the bounded security-only diff and unchanged
   migrations/business base.
2. Build a new artifact with the existing release builder and derived binding.
   Recompute archive hash, raw/canonical manifest hashes, full file/directory
   inventory and all seven consumer pins. No candidate artifact was built or
   installed by this code-only security task.
3. Independently approve the entry hash and exact byte manifest: all original
   nine pins, source/tree/parent, absolute trusted roots, new `application_exact`
   and `binding_sha256`. The entry/byte manifest is NOT an Owner approval.
4. Obtain independent Owner policy/risk acceptance, Root technical authorization
   and Production deployment approval with distinct audit IDs. Have an authorized
   Root operator verify the authenticated decision sources; role strings or TTY
   alone do not establish Owner identity. Seal then explicitly reconcile under
   the approved operation window. No real records are produced in this task.
5. Retain all 18 actual source-bound evidence checks, current Schema015,
   API/MCP/Worker health, backup/restore, exact 562 data-compatible rollback,
   independent deployment approval and the three explicitly reviewed risks.

## Test interpretation and review gate

The local ordinary-UID Linux suite exercises actual producer seal/reconcile,
release preflight, recovery target and rollback qualification calls with real
atomic files, fsync/readback, process interruption and subprocess locking.
Database/service reads and Root/issuer identities are labelled fixtures; this is
NOT actual Production Native, Root attestation or PG validation. No test result
authorizes installation or release. Network calls are denied by the runner.

The original 04 attack inputs (initializer, bytecode, reused audit ID) remain
unchanged and are rerun. The two old frozen-consumer vulnerability observations
remain historical tests, not closure assertions. New tests assert rejection in
the actual current consumer chains, including revoke-before-move, unsafe/missing
states, code drift, fsync/readback failure, repeated coordination and both lock
orders. Final exact-source JUnit counts and byte hashes accompany the handoff.
04 must independently review this new exact identity; prior QA is not inherited
as approval. There is no policy approval, installation or release readiness claim.

Pre-commit joint run: **619 passed, 0 failed, 0 errors, 0 skipped**. This comprises
the inherited 499 (including two historical blocker observations), 17 additional
verified-entry cases, 35 shared-state cases, 50 actual consumer-entry cases,
10 producer/consumer joint cases and eight actual frozen-reader cases. The three
original 04 attacks are included in the inherited count. A fresh archive of the
final commit must repeat this exact suite before code-candidate handoff.

# Historical 664 result — PARTIAL / BLOCKED (superseded above)

This successor is a local review candidate with direct parent
`59d34cd8099b766fdd777284f4961d25810473a9`, tree
`f1931dd4b1e502cd89eb6d3ff28681d13ac3e1e1`. The frozen application remains
7dc / tree `1d332373bd8cc31da074456e9e9bcaaa4aceb948`; 280a business code,
the seven V3 consumer pins, all V2/V3 consumer code, seals and 18 evidence
requirements are unchanged. The final successor identity is the Git commit
containing this document, recorded independently in the local handoff evidence.

**Do not execute the historical entry examples below.** Direct execution of
`v3_trusted_record_bootstrap.py` now rejects before repository imports. The new
verified entry is a candidate, not an approved trust root. All real `--seal`
operations are explicitly blocked, including when an operator supplies valid
record bytes. No installation or Production entry was added.

`FIRST_REMAINING_BLOCKER = FROZEN_V3_CONSUMER_DOES_NOT_OBSERVE_COMMIT_UNCERTAINTY`

## A. Verify bytes before importing candidate code

After future independent adoption of this exact entry, an authorized operator
would first verify `v3_bootstrap_verified_entry.py` using existing trusted system
`sha256sum` and an expected hash from the independently approved channel. This
initial check must happen BEFORE Python executes that file; its own checksum
check is not a substitute or authorization. Use trusted system Python/stdlib,
`-I -B -S`, no developer environment, and the Root-owned approved absolute path.

The entry verifies an independently hashed, Root-protected byte manifest:
`schema_version=1`, `scope=V3_VERIFIED_SOURCE_ONLY_ENTRY_V1`, `source_commit`,
`source_tree`, `parent_source_commit=59d...`, exact `tooling_root`,
`git_repository`, the original nine `code_pins`, and `entry_sha256`.
This is a byte identity manifest, NOT an Owner or deployment approval.

It verifies exact Git identities/blobs and trusted ancestors for ALL nine inputs,
rejects candidate bytecode and scripts initializers, then compiles the captured
bytes of seven executable modules. `app/settings.py` and rollback JSON remain
data only. The synthetic `scripts` namespace executes no disk initializer. The
sole import finder permits only captured repository sources and trusted system
stdlib; app modules, other scripts and top-level repository import fallbacks
reject. Existing nine pin names/contract are retained; the entry has a separate
initial independently approved pin, not a self-issued approval.

Review-only invocation format (not permission to run as Root):

```text
/usr/bin/python3 -I -B -S <independently-verified-entry> \
  --pin-manifest <protected-exact-byte-manifest> \
  --independently-approved-pin-sha256 <independent-expected-sha> -- \
  <original-action-and-exact-arguments>
```

No UID override, alternate import-root bypass or fixture mode exists in the
formal CLI. Permission metadata is doubled only in ordinary-user test code.
Actual subprocess/import/canary execution is exercised by those tests.

## B. Typed independent approval identities

The frozen Owner policy scope, Root technical workflow, and Production deployment
scope are distinct types. All three audit IDs must differ. Existing protected
Owner and Deployment records and prior immutable technical-decision snapshots
are checked before mutation, under the global lock, and after TTY latency.
Policy audit/hash references cannot masquerade as the deployment's identity.
Cross-type/cross-operation reuse rejects. Only the byte-identical revoke
technical decision may enter the narrowly bounded revoke continuation checks.

Neither a role/principal string nor Root TTY authenticates the Owner. Actual
independent issuer identity, authority, exact scope, freshness and revocation
remain human checks in the separately approved authenticated channel. No Owner
signature producer, key store, approval platform, database or service was added.

## C. Revocation interruption and commit uncertainty

Under the existing global lock, valid revocation first creates and fsyncs a
protected pending directory named by the independently pinned technical-decision
SHA, then withdraws the exact Policy into it. Original bytes remain unchanged. Only after withdrawal
and directory fsync/readback can revoke intent/audit records be published.
Thus the original 04 technical-audit failure leaves no active Policy for the
frozen consumer. A retained pending namespace blocks further producer sealing.
The pre-withdrawal crash window still leaves the frozen consumer able to read the
Policy: it does not observe that pending namespace. This is the same unresolved
consumer-contract blocker below, not a claim that every revocation window is closed.

Same-decision retry verifies exact bytes, scope, audit identity, pending/archive
state, inode-safe protected reads and fsync/readback. It can finish missing audit
steps and withdrawal, never reenable policy, erase the pending namespace, overwrite
history or create a new approval. Completed retries are idempotent. Changed
grant, policy or audit refuses. Expired grants still refuse; future expired-state
recovery needs explicit authority, never silent lifetime extension.

Writes retain immutable intent/completion linkage. Any protected mutation that
raises reports `COMMIT_UNKNOWN`, action and attempted phase, with
`writes=MAY_HAVE_COMMITTED`; it is not a zero-write BLOCKED claim. Pending
operations prevent another producer seal. Exact revoke continuation performs
independent read/fsync/read before accepting an existing write. Ordinary approval
publication never blindly retries or treats visible bytes as a new approval.
Even a visible completion requires independent protected read/fsync/read of the
intent and completion; persistent fsync/readback failure remains COMMIT_UNKNOWN.

**The required global UNKNOWN consumption guarantee is NOT closed.**
`Rollout._atomic` performs `os.replace` before directory fsync; Store readback
also follows replace. A failure after replace can leave authentic 0600 single-link
approved bytes visible. Frozen `schema015_successor_binding.qualification` and
`_p0_qualification` read only the three approval/evidence files; they never consult
producer intent/completion/UNKNOWN. This also affects the existing recovery
consumer. The lock is released on process death. A new producer marker or a
best-effort metadata rollback cannot change that fact or guarantee crash safety.

The smallest remaining capability is a separately approved, exact-Source-bound
consumer contract extension requiring verified terminal persistence state and
rejecting PENDING/UNKNOWN/REVOKING in release AND recovery under the same lock.
That changes frozen consumer bytes/code pins and would require explicit rebinding
and artifact review. It was NOT implemented or authorized here. Do not replace
7dc pins, manufacture a completion, delete traces, or add a parallel authority
wrapper to get a PASS. The real CLI `--seal` stops on this exact blocker.

## Regression and evidence boundaries

Final combined targeted regression: 499 PASS / 0 FAIL / 0 ERROR / 0 SKIP.
Breakdown: inherited 415 (91 bootstrap + 128 V3 + 85 V2 + 111 atomic tooling),
19 approval IDs, 17 persistence, 43 source-only entry, original 04 three failing
probes unchanged (3 PASS), and 2 frozen-consumer blocker observations.
Therefore **497 passing component test cases + 2 reproduced blocker observations**;
the two observations are NOT security acceptance PASS for UNKNOWN consumption.
No old negative assertion was removed. One existing ordering assertion adds the
new completion record and verifies its linked terminal state.

Retained 59d evidence: `.v3-bootstrap-59d-independent-qa-v1/` in the main workspace.
It is unchanged. Current evidence: `.v3-bootstrap-security-fix-v1-evidence/`.
Initial test setup mistakenly used the parent of the retained artifact directory
(342 PASS / 112 setup ERROR); corrected using the actual frozen hash-matched
archive. An intermediate run had 482 PASS / 15 FAIL: six revoke namespace glob
collisions and nine source-entry import/test-fixture defects, repaired without
weakening assertions. Both raw JUnit files are retained, separately from final.

Execution: ordinary lucky UID1000, private local Linux POSIX directories,
Python3.11.16, actual files/rename/fsync/flock/subprocesses, explicitly fictional
actors/approval/report fixtures. Network/HTTP blocked. No Root, PRIMARY/Test
service or DB, Production, Provider, WeChat or Image action. No Stage2, full
pytest, Candidate Build or real Native claim. Git local commit only; no Push.

No READY state is claimed. Required next decision is limited to the frozen
consumer contract boundary above, followed by 04 independent review and actual
Owner/Root/Production approvals separately. No broader governance work is proposed.

---

# Historical 59d proposal (retained context, superseded entry and readiness)

Status: `READY_FOR_V3_TRUSTED_BOOTSTRAP_REVIEW` means code and the proposed
manual-attestation contract can be reviewed. It does NOT mean Root use,
installation, policy change, deployment, budget or final release is approved.

## Exact object and trust boundary

The application/artifact stays frozen:

- Release `20261012-7dcde57-p0-controlled-v3`.
- Source `7dcde5736c9493b5862f465848afb585691b721f`.
- Tree `1d332373bd8cc31da074456e9e9bcaaa4aceb948`.
- Parent `280ae327a9d4720c5cadfac38aba259c7ff161ae`.
- Archive SHA256 `82b52d58300a0545b14c334f3e5be2d4878ade74c39ad5f7771330d1233f776e`.
- Raw manifest SHA256 `ad6c2d444950045e4143deb30b9c3bfe8caf01dca48be5074cd75e26d92380ce`.
- Canonical manifest SHA256 `71c5b8fdccd41d75cfb068b84cd86562fd8173351881c46542c6acfb6ed56bc8`.
- Binding SHA256 `040ac73cf432184b3dca93121a9336ee2e723f91b275b633f6c43047e0a1a3e1`.
- Host `iv-yekqs6ez9cnu262hhsg0`; exact predecessor 562 from the unchanged binding.
- Install target `/opt/enterprise-agent-workbench/releases/20261012-7dcde57-p0-controlled-v3/`.

Evidence read: `.p0-v3-trusted-owner-handoff-v1/` and
`.p0-v3-final-validation-v1/` in the shared local workspace. These are explicitly
NOT approvals. They prove a bounded host survey found no authorized compatible
record producer. The old inactive installer is fixed to a 20260923 release and
the 63112f8 predecessor, so it is not reusable unchanged for 7dc/562.

The new independent Tooling successor adds only
`scripts/v3_trusted_record_bootstrap.py`, its tests and this review document.
It is NOT another application release and is NOT put inside the 147-file
7dc installation. All application, migrations, seven consumer pins, V1/V2,
historical seals and existing release/switch/recovery files stay byte-identical.

Reuse: unchanged `schema015_successor_binding.protected_json` and
`_p0_qualification`; unchanged `runtime_policy_rollout.Rollout._atomic`,
`_read`, `_directories`; the frozen Root-reader's same strict metadata predicates
also check artifact/directory inputs outside BASE; the SAME global Release lock
inode. No historical Test-only helper absent from the exact Source is imported.
No new database,
keys, trust registry, server, background service, deployment or recovery runner.

## Minimum formal trust-root decision still required

Owner and the existing Production Root-maintenance authority must explicitly
adopt **V3_ROOT_MANUAL_ATTESTATION_V1** for this exact pair/host, appoint the
actual responsible operators, and approve the independent Tooling Source/Tree
and its dependency pins BEFORE loading it as Root. This is a proposed bounded
extension of the existing independent-checksum/manual Root trust introduction,
NOT something this program approves for itself.

There is no evidenced existing Owner digital-signature/key verifier. This
proposal therefore requires the authorized human Root operator to verify the
actual Owner/Release authority and exact decision in an **independently
authenticated, archived channel**. The authority must approve that manual
verification standard and the actual channel/retention mechanism. Do not create
keys or assert a fictitious signing system. If the authority requires automated
cryptographic Owner verification, this candidate is insufficient: STOP for
that exact trust-root decision; do not substitute a fabricated JSON signature.

An `APPROVED` label, principal string, Root UID, SHA learned from the same file,
chat intent, or successful component test is NOT independent authorization.
The operator verifies actual identity, role, exact scope, freshness and current
revocation in the independent channel; then attests on `/dev/tty`. The tool
records that attestation and preserves the original technical decision bytes.
It verifies integrity and scope, NOT the legal identity of the human issuer.

## Three separate decisions, never combined

1. **Owner policy decision**: exact V3 replacement map, all three specific risks,
   exact Source/Tree/archive/manifest/binding, test and Production identities,
   real principal/audit/time. The intended policy-record bytes are approved by
   the Owner; Root copies those bytes verbatim, not synthesizing `APPROVED`.
2. **Root technical decisions**: separately approve use of this Tooling and the
   exact inactive-install/record-sealing scopes, host, real operator, inputs and
   independently reviewed SHAs. No permission to switch, restart or write DB.
3. **Production release decision**: issued independently only after all 18 real
   proofs are verified; binds policy raw SHA/audit and evidence raw SHA. Its
   archival sealing is NOT execution. Budget and actual deployment are later.

Different decision records/audit IDs are mandatory; do not invent a second
natural person. Actual authorized people and institutional responsibilities
must be identified by the established human governance process.

### Technical record schema (not an issued sample)

Strict field set, no extra fields:

`schema_version=1`, `workflow=V3_ROOT_MANUAL_ATTESTATION_V1`,
`authority=PRODUCTION_ROOT_MAINTENANCE_AUTHORITY`, `decision`, `principal_id`,
`audit_id`, `issued_at`, `expires_at`, `host`, `operator`, `action`, `candidate`,
`binding_sha256`, `producer`, `input_sha256`,
`independent_decision_references`, `production_switch_authorized=false`,
`business_database_write_authorized=false`.

`producer` is the exact independent Tooling commit/tree plus `CODE_FILES`
pin map, including its transitive code/data dependencies. `candidate` is the
seven-field frozen 7dc identity. `input_sha256` binds each actual input byte
stream; for release-record sealing it also binds `report:<check>` for all 18
original reports. References identify independently authenticated decisions
and report reviews, not locally invented assertions. A technical decision
cannot predate or reuse the audit ID of the Owner/release decision it seals.

Policy/deployment/evidence schemas remain exactly those enforced by frozen
7dc. Expiry is at most 24 hours. Evidence observed time is at most 24 hours old.
Deployment cannot outlive policy. Do not adjust old times to obtain freshness.

## Actual entry, verification and record publication

After separate real authorization, in the approved existing Root-owned reviewed
operations workspace (the authority must provide its exact path), retain a
Root-protected Git repository with the frozen application and independent
Tooling objects. Independently check the bundle hash, `git fsck --strict`, both
commit/tree/parent identities and Tooling dependency hashes with trusted system
tools **before importing any candidate Python code**. Do not infer the expected
hash from the untrusted candidate. No `PYTHONPATH` injection or developer env.

Use `/usr/bin/python3 -I -B -S` and the exact reviewed script path. The script
uses only stdlib/its reviewed modules; no application Settings initialization,
Secret reading or external network is required. CLI default is dry-run.

```bash
/usr/bin/python3 -I -B -S "$REVIEWED_SCRIPT" inspect-artifact \
  --git-repository "$REVIEWED_GIT" --archive "$EXACT_ARCHIVE" --manifest "$EXACT_MANIFEST"

/usr/bin/python3 -I -B -S "$REVIEWED_SCRIPT" seal-policy \
  --git-repository "$REVIEWED_GIT" --archive "$EXACT_ARCHIVE" --manifest "$EXACT_MANIFEST" \
  --producer-source "$REVIEWED_TOOLING_SOURCE" --producer-tree "$REVIEWED_TOOLING_TREE" \
  --technical-grant "$INDEPENDENT_TECHNICAL_DECISION" \
  --independently-approved-technical-sha256 "$EXTERNAL_APPROVED_TECHNICAL_SHA" \
  --operator "$REAL_AUTHORIZED_OPERATOR" --policy "$INDEPENDENT_OWNER_RECORD"
```

These variables are formal operator inputs, not supplied by an application,
model or this proposal. Dry-run claims byte validation only and performs no
human authority verification/publication. Only a separately approved operation
adds `--seal`; no piped `yes`, `approved=true` flag, force, path override or
unattended signature generation is available.

Outputs (separate from the 147-file install):

`shared/release-qualifications/<rid>.policy-approval.v1.json`,
`shared/release-qualifications/<rid>.evidence.v1.json`,
`shared/release-qualifications/<rid>.approval.v1.json`.

Root:Root, 0600, regular, single-link, no symlink ancestor, no peer-writable
ancestor, <=256KiB, unique JSON keys. Original independently approved bytes
are copied unchanged. Audit/consumption is under existing
`shared/release-state/v3-record-bootstrap/`, separately included in Root scope.
It binds host/operator, producer, exact artifact, original technical decision,
authenticated-channel references and each input SHA.

For release-record sealing, replace `seal-policy` with `seal-release-records`,
provide `--deployment`, `--evidence`, `--reports` and its NEW separately approved
technical decision. `<check>.report` must contain each original report's bytes,
Root-protected; all 18 SHAs must match. Frozen consumer validation runs before
publication, again under the global lock and again after TTY acknowledgement.
The human MUST inspect report contents, not just pass labels/hash formats.
Publish evidence first and deployment approval LAST. Policy alone never grants
release; evidence-only partial publication also cannot grant release.

To independently reverify, rerun without `--seal` against the actual protected
record bytes and the retained original technical decision, with its SHA obtained
from the independent authority channel. Verify audit input hashes and producer
identity as well. This does not re-consume or issue another decision.

Source-wide per-action intent, decision raw SHA and technical audit ID are
consumed once before publication. Source intent is first, covering interruption
even before the technical snapshot is saved.
Duplicate sealing, including a new technical audit replaying the same Owner
record after interruption, rejects. Partial output/audit is retained, no auto
cleanup/retry. Independent authorization must resolve a partial operation;
never erase consumption markers to retry. This one-use control governs **record
sealing**, not permission to execute Production: the existing deployment path
must still have its own explicit one-operation approval, lock, commit receipt
and recovery audit. Consumer rereads during the SAME release/recovery operation
are validations, not new authorization. Production execution/replay acceptance
is NOT proven by these component tests.

For an independently approved withdrawal, use `revoke-policy` with the exact
active policy bytes and NEW technical decision, default dry-run, later `--seal`.
Withdraw the policy atomically into the protected audit archive, fsync both
directories and retain its original bytes. The unchanged consumer now rejects
because its required policy is absent. A revoked-source marker and consumption
records forbid resealing. No old Approval/Seal is rewritten. Errors/interruption
STOP; no automated reenabling, deletion or reconstruction of authorization.

## Independent inactive installation — no new installer

This reuses deploy/README.md's manual Root checksum/unpack workflow. It still
requires a SEPARATE real technical install decision and actual Root audit; a
successful `inspect-artifact` result does not authorize these steps.

1. Root independently verifies real decision/operator/host/window/revocation,
   application Source/Tree/Parent, archive/raw/canonical/binding and seven pins.
   Preserve the decision and actual authenticated-session/audit reference.
2. Acquire the existing `/run/lock/enterprise-agent-workbench-release.lock` with
   nonblocking exclusive `flock`, holding it for the WHOLE install. All Root
   writers must cooperate; reject an untrusted lock inode/parent or any busy
   operation. Recheck Source and the real current pointer under the lock: it
   MUST still resolve to the precise unchanged 562 application directory.
   A Root-owned sticky system `/run/lock` is accepted ONLY with an already
   existing safe Root single-link lock inode and stable post-flock inode check;
   no new inode is created there. A plain peer-writable parent is rejected.
   Qualification, audit and other input paths still require strict non-peer-
   writable Root ancestors, never the sticky-directory exception.
3. Verify all ancestors Root-owned, real directories, no peer write; the exact
   target must NOT exist, including a dangling symlink. Do not overwrite or
   auto-remove a partially installed target. Set `umask 0022`, create only the
   exact release container at 0755.
4. Unpack only the already inspected archive using existing system `tar`,
   `--keep-old-files --no-same-owner --no-same-permissions`. Git archive carries
   0664/0775 headers; umask0022 MUST produce 0644/0755. Preserve source executable
   bits. Copy archive and raw manifest with trusted `install -m 0644`, fixed
   filenames. No temporary shell installer or changes to old562 files.
5. Read back EVERY path/type/byte/hash/mode/uid/gid/nlink, reject extra files,
   links and permission drift. Compare 145 payload files with exact Git bytes
   except the existing build_release static-index SHA substitution; verify its
   two deterministic JS/CSS hash references. Verify all seven pins again.
6. Inventory MUST be 147 files and 27 directories including the container.
   Root:Root, directories0755, files0644/executables0755. Recheck current still
   exact562, and services/DB untouched. Preserve the actual installation audit
   through the authorized existing channel and its independently reviewed SHA.
7. On any failure retain artifacts/audit and STOP. Installation grants NO
   current switch, service restart, DB write, final acceptance or Smoke budget.

The reviewed Tooling execution workspace and later qualification/audit records
are separate scopes requiring real Root approval, not extra files silently
placed inside the 147-file artifact install. Their exact operation paths and
operators must be supplied by the existing Root authority, not invented here.

## Remaining hard gates and results interpretation

All 18 real checks in `binding.P0_CHECKS` remain mandatory, including fresh
Production015, actual API/MCP/Worker Source/PID health, encrypted Production
backup + independent restore, exact562 data compatibility and pre/postcommit
recovery, original technical/business Smoke with separately approved budget,
data preservation and exclusive-lock audit. Existing real PG/API/SSE proof from
02 is retained; missing real proofs are not manufactured by this program.

Owner explicitly reviews the three fixed risks:
`FULL_STANDARD_STAGE2_NOT_PROVEN`,
`DEDICATED_SYSTEMD_SERIAL_NATIVE_REHEARSAL_NOT_PROVEN`,
`REAL_PROVIDER_BUSINESS_QUALITY_NOT_PROVEN`. No default risk acceptance.

Code tests run only as ordinary user in marked private Linux files, using fake
Root metadata/actors/reports, actual atomic filesystem writes/flock and the
genuine already-built 7dc archive/Git objects. They are NOT Root, issuer,
installation, Production, PG, Native or Provider evidence. The initial archive
probe correctly exposed two existing build details (0664/0775 headers and
deterministic static-index substitution), now verified without editing 7dc.

No bootstrap circular dependency on old562 accepting V3: independently approved
system-tool verification introduces Tooling; authentic Owner decision is sealed;
independent manual inactive install introduces 7dc; missing proofs are completed;
independent release decision is sealed; ONLY THEN the unchanged controlled
release entry may consume V3 under separately granted execution authority.
The program never signs its own introduction or final activation.

First remaining authority blocker:
`V3_ROOT_MANUAL_ATTESTATION_WORKFLOW_AND_EXACT_TOOLING_NOT_APPROVED`.
Actual Root proof, installation and all remaining release gates are NOT done.
Do not label this proposal `INSTALL_READY` or `PRODUCTION_RELEASE_READY`.
