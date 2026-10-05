# Migration 015 exact runtime recovery V1

Scope: only `014 -> 015_chat_image_attachments.sql`, frozen feature
`b25fc4381ce5fa2d9a35325155953a9f5cb0d2e4`, and exact predecessor
`20261004-c2f4ec7-image-format-v1` / `c2f4ec797c3eff4fdf62665bf3e7b47cd3167608`.
The predecessor's full Git tree correlation is pinned in the reviewed contract;
a selected-file tar archive is not represented as a full Git tree.

Build input (a later authorized Candidate cycle, **not** this tooling task):
`--forward-migrations deploy/forward_migrations_015.json`. The declaration and
`migration_015_recovery.v1.json` must be inside the immutable installed release.
The old V1 `012 -> 014` contract remains a separate historical path, not an
additional direct predecessor for this V2 plan.

## Transaction and commit receipt

The existing `release_switch.sh` retains the global non-blocking flock through
preflight, migration, code activation, smoke, final state and recovery evidence.
Its phases are `PRE_COMMIT`, `MIGRATION_APPLIED`, `RUNTIME_SWITCHED`, and
`POST_COMMIT_HEALTH`. Before disarming rollback, the 015 path records an exact
commit receipt in `shared/release-state/<release-id>.json` (private directory,
file mode 0600). The receipt binds the release/source/canonical manifest,
reviewed recovery contract and exact committed Binding/Manifest state.

013/014 and 015 are never implicitly combined. The 015 migration is a single
PostgreSQL transaction with its ledger entry. Historical revision fingerprints
are compared before/after. Any unknown inventory/history/checksum blocks.

## Post-commit entry

After separate operational authorization, invoke the **new committed release's**
unchanged official entry with its approved public identity pins in the standard
`RELEASE_EXPECTED_SOURCE_COMMIT`, `RELEASE_EXPECTED_ARCHIVE_SHA256`,
`RELEASE_EXPECTED_RAW_MANIFEST_SHA256`, and
`RELEASE_EXPECTED_CANONICAL_MANIFEST_SHA256` variables:

```bash
bash /opt/enterprise-agent-workbench/releases/<approved-new-release>/enterprise_agent_poc/deploy/release_switch.sh \
  <approved-new-release> --rollback-runtime
```

This is not a command to execute during the tooling review. No caller-selected
target, directory, future migration, schema down, SQL compensation, Binding
transition, Agent productization, Registry staging or Provider call is accepted.

The entry requires current installed/archive/raw/canonical identities, the
commit receipt, exact predecessor package identity, exact 001-015 ledger,
pending 0 and the member-status floor. A crashed service can be recovered;
a live foreign CWD/module/executable or unknown/mixed Binding state cannot.
Recovery guard uses the **original committed** bindings, not newly accepted
drift. Service activation reuses the existing controlled configuration writer.

After recovery, the trusted new gate (not the old strict `migrate.status`)
verifies predecessor installed Source, actual API/MCP/Worker CWD/module/exe,
API/MCP/Redis health, Registry, Binding/Manifest, schema 015 and pending 0.

`RECOVERY_MODE = PREDECESSOR_ON_SCHEMA_015`: **code rollback != schema rollback**.
No migration ledger or 015 object is removed. A failure returns
`POST_COMMIT_RUNTIME_ROLLBACK_FAILED`, keeps the recovery snapshot/evidence
under the lock, and performs no further automatic fallback/retry. Success is
`POST_COMMIT_RUNTIME_ROLLBACK_PASS`; schema_rollback remains false.

## Proof boundaries

Targeted fault tests cover identity/schema/compatibility rejection, live foreign
CWD, restored CWD and restored-health failures, lock coverage, evidence retention
and no second recovery attempt. Native isolated rehearsal uses real Bash/flock,
POSIX symlink, independent PG16.6 UTF8/C, private Redis, and distinct native
systemd services inside the Test resource slice. Its synthetic health fault is
an actual HTTP503 **after** the synthetic Commit Point.

Only Test OS paths/unit names/ports and test-mode health expectations are
relocated by the existing rehearsal-boundary adapter. Config/auth/Technical
Smoke is a no-Provider fixture boundary, not a Production smoke PASS. The
identity/ledger/migration/rollback/state/receipt logic is unchanged. Native
local-hash `knowledge=degraded` is explicitly expected and does not prove
Production embedding readiness. A later real Candidate still needs the full
approved Release gates. This contract grants no Production execution authority.
