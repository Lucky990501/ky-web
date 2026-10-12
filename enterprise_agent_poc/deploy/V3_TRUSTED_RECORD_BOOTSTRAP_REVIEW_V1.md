# V3 protected-record bootstrap — proposal, NOT effective authority

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
