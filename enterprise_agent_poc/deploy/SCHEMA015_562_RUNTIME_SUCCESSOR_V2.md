# Schema015 exact 562 runtime successor (V2 candidate)

This is a bounded extension of `release_switch.sh`, `release_verify.py`,
`schema_015_runtime_only.py` and `release_runtime_recovery.py`. There is no new
release operator, external Tooling loader, issuer, installer or migration.
History keeps the V1 519 recovery contract and its evidence unchanged.

## Scope and identity

The exact predecessor is `20261007-562f201-reference-dependency-retry-v1`,
Source `562f201e362f0b10ae4fe3a8dae9a8c4d9ef91d3`, associated Git tree
`e5205965e57fce6ce2b65dd747a110d00f7f1b1a`. All six full identity fields,
including archive, raw Manifest and canonical Manifest, are pinned in
`scripts/schema015_successor_binding.py`. Those artifact pins came from the
2026-10-12 13:09 Production metadata attestation. That attestation did NOT
prove a live DB ledger or a complete installed Git tree.

Only an explicit `runtime_only_release.schema_version=2` selects this contract.
Migration NONE, Schema015, zero pending/unknown/checksum drift and
`member_account_status_v1` remain mandatory. Binding/Productization and forward
migration declarations cannot be combined. V1 retains its original semantics.

The new seven-file Tooling hash set must be integrated into and packaged with
the final reviewed Application. Helpers still execute exclusively from the
controlled Candidate directory. This is NOT an independently activated external
Tooling runtime or permission to choose an arbitrary PYTHONPATH.

## Offline preparation (not approval)

After the final exact Application is committed, run from its own checkout:

```sh
cd enterprise_agent_poc
python -B scripts/schema015_successor_binding.py prepare \
  --successor-source FULL_FINAL_COMMIT --output /offline/evidence/binding.json
```

The output path must not exist. The command resolves the full Git Source, tree,
one exact parent and seven Tooling hashes. It requires 562 ancestry and identical
001-015 migration inventory/blobs. It generates only a non-protected declaration
for the existing builder's `--runtime-only-release` input. It does not issue a
permit or install anything. Different final Source/tree/parent or Tooling pins
produce a different binding and require new qualification and approval.

Build with the existing source-bound builder only after the formal Linux Test
scope is actually valid. Candidate archive and raw/canonical Manifest identities
are then included in the qualification. No old Source's tests or approval are
automatically inherited. The staged e551 and future 03 successor are not yet
qualified by this Tooling candidate's component tests.

## Additional protected qualification (proposal, not signed in this task)

V2 requires these two exact files under the existing controlled release base:

`shared/release-qualifications/<release_id>.approval.v1.json`

`shared/release-qualifications/<release_id>.evidence.v1.json`

The proposed format must be reviewed and produced by an actually authorized
Root evidence/approval process. This candidate implements ONLY its read-only
consumer. It does not claim an approved compatible signer already exists. An
operator must NOT hand-write PASS fields to manufacture Native evidence.

The reader requires every ancestor Root-owned and not group/other writable;
each file is a NOFOLLOW, single-link regular file, Root-owned, mode0600, at most
256KiB, with no duplicate JSON keys. Symlinks or lucky-owned ancestors fail.

Approval exact fields: `schema_version=1`, scope
`SCHEMA015_EXACT_PAIR_RELEASE_RECOVERY`, `issuer_uid=0`, actual `audit_id`,
actual `issued_at` and `expires_at`, `binding_sha256`, `candidate`, `predecessor`,
and `evidence_sha256`. Future, missing or expired approval is rejected. The
candidate binds release ID, full Source/tree/parent and archive/raw/canonical
Manifest identities. Predecessor must equal the full fixed 562 identity.

Evidence exact fields: `schema_version=1`, scope
`ISOLATED_NATIVE_SCHEMA015_EXACT_PAIR`, the identical `candidate`, `predecessor`
and `binding_sha256`, exact full015 `schema_fingerprint`, `schema=015`,
`pending=0`, `migration_action=NONE`, `data_contract=member_account_status_v1`,
and actual PASS checks for Full pytest, Stage2, Source installation,
API/MCP/Worker Native, exact562-on015, pre-commit failure recovery, post-commit
recovery and business-data preservation. Component/Mock evidence is insufficient.

The original four `RELEASE_EXPECTED_*` approval pins, installed archive checks,
readonly PG16 ledger/checksum/epoch/data-floor gates, Registry/Binding state,
Global Lock and Commit Boundary still apply. Missing qualification fails before
release mutation. No Production secrets are needed for offline preparation.

## Recovery and remaining operational gates

V2 resolves only its own exact Candidate or pinned 562 target, never 519, a
wildcard predecessor or an arbitrary release. Post-commit receipts retain the
exact predecessor, current binding digest, live ledger digest and original
Binding state. Unknown/mixed state fails closed. Schema down, cleanup of business
data and manual pointer changes are not permitted.

Both runtime and data remain Schema015, no migration. Actual Native rehearsal
must demonstrate preserved Task/Run/image/reference/credits/Agents/Skills and
tenant data. No such rehearsal was performed by the component tests. A new
Root-sealed qualification and exact final-Source deployment authority are still
required; neither is issued here. Expiry during recovery remains fail-closed,
requiring lawful fresh recovery authority rather than a bypass.

Production/PRIMARY pointers, services, DB and historical artifacts are unchanged.
