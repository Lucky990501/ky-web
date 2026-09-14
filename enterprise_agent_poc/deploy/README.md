# Workbench controlled deployment

Workbench production uses immutable Release directories and a controlled
systemd switch. Never copy a development working tree over the production
tree, overwrite `release-current`, or copy a developer `.env` to the server.

## Cross-platform source policy

Windows, macOS, and Linux checkouts are governed by the repository
`.gitattributes`. Cross-platform source, SQL, shell, configuration, and
documentation files use LF; Windows-native `.bat` and `.cmd` files use CRLF;
binary files are excluded from text conversion.

Each clone should use repository-local Git settings rather than changing the
developer's global Git configuration:

```bash
git config --local core.autocrlf false
git config --local core.eol lf
```

Before changing development machines, commit and push on the first machine.
On the next machine, require a clean worktree and synchronize with:

```bash
git fetch origin
git checkout master
git pull --ff-only origin master
git status
```

## Reproducible Release build

Build from one explicit, clean Git commit. `build_release.py` selects only the
release whitelist, uses deterministic gzip metadata, rejects tracked secrets
and platform metadata, and can write a Release manifest containing the source
commit, archive SHA-256, selected files, and build platform:

```bash
python scripts/build_release.py \
  --commit <40-character-commit> \
  --output <release-id>.tar.gz \
  --release-id <release-id> \
  --manifest-output <release-id>.manifest.json
```

The archive and manifest must be independently checksum-verified after upload
and unpacked into `/opt/enterprise-agent-workbench/releases/<release-id>`.

## Migration checksum gate

Migration checksums normalize only `CRLF` and isolated `CR` to `LF`. No spaces,
blank lines, comments, case, encoding, or SQL content are otherwise changed.
New migrations record the canonical LF SHA-256. Historical Windows checksums
remain untouched and may be accepted only as
`LEGACY_LINE_ENDING_COMPATIBLE` when they exactly match the deterministic CRLF
form of the current canonical bytes.

Production `migration status` must contain only `EXACT_MATCH`,
`LEGACY_LINE_ENDING_COMPATIBLE`, or an explicitly expected `PENDING` migration.
Any `CHECKSUM_MISMATCH`, unknown history version, invalid filename, duplicate
version, or sequence gap blocks the Release. Do not update
`schema_migrations`, rerun an applied migration, or use a force bypass.

## Production switch sequence

1. Confirm the current Release and API/MCP/Worker health on the Linux server.
2. Verify the uploaded archive SHA-256 and Release manifest.
3. Run candidate `deploy/release_switch.sh <release-id> --preflight-only`; its
   read-only migration gate rejects mismatches/unknowns without status CLI DDL.
4. Verify runtime dependency health and the shared configuration fingerprint.
5. Run `deploy/release_switch.sh <release-id>` as root.
6. Require API, MCP, and Worker to be active and `/api/health` to report
   `status=ok`, `knowledge=ok`, and `environment=production`.
7. Complete authenticated browser acceptance. On health failure the switch
   script restores the previous application only after trusted rollback gates
   pass; a blocked/failed recovery retains its snapshot for manual intervention.

## Approved-schema application rollback

After schema upgrades, run deployment/rollback tooling that knows the complete
approved schema baseline. Never edit an old Release, rewrite migration history,
run down migrations, or bypass unknown/checksum protection. Old `migrate.py
status/up` and old release entry points intentionally continue to reject future
history; they are NOT the rollback-preflight authority.

From a trusted, independently archive-verified Release, check the old target:

```bash
bash deploy/release_switch.sh <trusted-release-id> --rollback-preflight \
  <approved-target-release-id> <approved-target-40-character-source-commit>
```

This mode is read-only and performs no service/link/drop-in changes. It uses
the same shared configuration/DATA_DIR and normal Skill/config checks, then
`scripts/rollback_preflight.py` verifies the entire applied history, the target's
own historical migration identities, both controlled archive/file identities,
and the reviewed `deploy/rollback_compatibility.json` declaration. Caller-provided
directories or compatibility declarations are not accepted.

Declarations pin exact migration filenames/checksums, old source/Manifest/archive
identity and evidence fingerprints. Updating an approved schema or rollback
target requires a reviewed new declaration and isolated compatibility evidence;
neither a larger version number nor an arbitrary future range grants permission.
The V1 evidence is PostgreSQL 16 isolated legacy startup/minimal Fake Runtime
execution, not production Provider/RAG certification.
The historical `legacy_only` evidence is retained. Migration 011 adds the
permanent `agent_data_contract` Compatibility Epoch; current row count is an
inconsistency check, NOT the rollback floor truth. `productized_v1` permanently
rejects `20260913-6abccad`, including zero-row, disabled and terminal states.
The explicitly approved minimum V2 target is original `20260914-bd04dcb`, with
independent source/archive/Manifest identity and isolated Schema 001–011
evidence. No release timestamp/commit ordering grants compatibility.

## Explicit pre-Pilot Compatibility Epoch

Migration 011 touches only platform compatibility metadata and V2-write guard
triggers; it does not convert Agent data or change historical migrations.
Zero-V2 initialization is `legacy_v1`; existing V2 initializes upward with
`migration_detection` provenance (no invented Release identity). The database
forbids rank decrease, changing an advanced record, DELETE and TRUNCATE.
Productized Template creation has a service transaction check and DB guard.
New local SQLite control-plane initialization has minimal fail-closed parity,
never an automatic Pilot advance or default-database reset.

Future production migration and advance EACH require separate human approval.
After independently verified controlled tooling is installed and configuration
loaded only inside its process, the interfaces are:

```bash
python scripts/compatibility_epoch.py status
python scripts/compatibility_epoch.py advance --to productized_v1
python scripts/compatibility_epoch.py quiescence
```

These are interface examples, not authorization to run them on production.
No DSN, arbitrary identity/path override, reset, downgrade or delete is exposed.
First advance requires strict complete Schema 001–011, explicit approved current
Release/floor identity (including all three active systemd MainPID actual CWDs),
compatible evidence, zero V2 data and disabled Runtime
Test Policy. It locks the singleton and rechecks history inside the transaction.
An already-advanced record returns idempotently without updating its provenance.
Shared configuration must come from the normal controlled process loader; never
print/copy an environment file or credentials. Normal `release_switch` does NOT
advance Epoch. New current application identities require independent approval,
even if their code is newer than bd04dcb.

The only absent-table exception is a PRE-MIGRATION `--check-plan` with 011 not
applied; it explicitly evaluates the planned 011 initialization scope. Full
rollback/advance requires the singleton. An applied 011 with missing metadata
always blocks, including an attempted SQL rerun; no zero-row reset is allowed.

Pilot rollback is control-plane-only on the V2-compatible application: block new
V2 runs, disable Pilot Instance/Runtime Test Policy, drain workload and preserve
all V2 history. `quiescence` reads DB and Redis without mutation; active V2
Task/Test/Run, pending retry/delivery, processing lease or unknown queue entries
return `ROLLBACK_INCOMPLETE` (exit 2). Invoke only AFTER preventing new producers;
it is an observation, not an atomic stop command. The current queue's recoverable
lease/retry state is its pending/processing lists, not a separate TTL scheduler.
Never delete/relabel V2 history, down schema or switch to 6abccad to stop a Pilot.
Restart the same compatible Artifact, verify history, then separately approve
Policy/Instance re-enable. Backups must preserve Epoch, constraints and triggers;
restoring a newer history as `legacy_v1` is forbidden.

Normal `--preflight-only` remains read-only and may show pending migrations.
Before a real forward switch, an additional plan gate validates the applied
prefix and the complete approved future baseline/rollback target **before up**.
On switch/health failure, the full read-only rollback gate must pass before
restoring the old application and restarting services. Old production health
must then pass; otherwise do not report rollback success. A blocked rollback
retains its snapshot and requires manual intervention rather than an
unconditional unsafe old restart.

Successful rollback means **application Release = old, schema baseline = newer**.
No schema rollback occurs. New deployment tooling remains authoritative for
subsequent deployments. This entry point exposes independent rollback-preflight,
not an unaudited manual switch/force-recovery command.

## Runtime configuration verification

The shared production environment file is authoritative. Do not separately
inject RAG, database, or embedding variables through a systemd unit, shell
profile, or old deployment directory. Execute the verifier in a process that
inherited the same environment as the API service:

```bash
python scripts/verify_runtime_config.py \
  --environment-file /opt/enterprise-agent-workbench/shared/enterprise-agent.env
```

The verifier prints presence flags and fingerprints, never secret values.
`matches` must be `true`. Production secrets, database credentials, tokens,
and private keys must never be copied back to a development machine.

## Dedicated Runtime Test Policy rollout (Stage 2.12.1)

`scripts/runtime_policy_rollout.py` is a dedicated four-key transaction tool,
not an arbitrary env editor. This stage is **local isolated readiness only**;
production rollout requires separate authorization and trusted tooling delivery.
It does not advance Epoch, create Pilot data, switch Release, migrate schema or
alter Agent/Registry business logic.

The root is fixed to `/opt/enterprise-agent-workbench`; its authoritative input
is `shared/enterprise-agent.env`. There is no CLI env/root override, force or
ignore. Run as the existing production owner (root). Commands for a separately
approved future operation are:

```bash
python scripts/runtime_policy_rollout.py status
python scripts/runtime_policy_rollout.py plan --tenant TENANT --slug social-content-agent
python scripts/runtime_policy_rollout.py apply --tenant TENANT --slug social-content-agent \
  --expected-config-sha256 ORIGINAL_SHA_FROM_PLAN
python scripts/runtime_policy_rollout.py restore --operation-id OPERATION_ID
```

Only `ENTERPRISE_POC_AGENT_RUNTIME_TEST_PRODUCTION_ENABLED`,
`ENTERPRISE_POC_AGENT_RUNTIME_TEST_ALLOWED_TENANT_IDS`,
`ENTERPRISE_POC_AGENT_RUNTIME_TEST_ALLOWED_TEMPLATE_SLUGS`, and
`ENTERPRISE_POC_AGENT_RUNTIME_TEST_TENANT_ID` can change. Apply produces a
singleton Tenant/slug scope and verifies all three actual service environments.
Tenant syntax validation is **not** Tenant/admin authorization: a separately
authorized operator must first verify the exact Tenant and its platform_admin.

Status/plan do not create locks, backups, journals or restart services. The
parser never sources/evaluates env bytes; duplicate keys, invalid policy values,
multiline, continuation, substitution and unsupported quoting fail closed.
All non-target bytes remain unchanged. Unsupported existing non-target shell
syntax also blocks rather than guessing secret values.

Apply stores the complete original file (600) and a secret-free transaction
journal under the owned `shared/runtime-policy-operations` directory (700).
Metadata includes original SHA/size/mode/owner, timestamp, operation ID and
application identity. Writes use same-directory temporary files, fsync,
inode/bytes/metadata CAS, atomic replace and directory fsync.
Apply requires the original config SHA emitted by plan, so a changed file between
commands blocks rather than silently accepting a new baseline. Restore compares
current bytes with the operation's post SHA and verifies the complete backup.
Cooperating invocations serialize through flock. CAS cannot make an unrelated
non-cooperating privileged writer atomic: all shared-env writers must obey the
exclusive change window; detected concurrent modification is never overwritten.

The tool restarts only the existing API/MCP/Worker units, checks unchanged
Release/Manifest and actual process CWD, fingerprint/policy agreement, and
local/public production health. Any restart/fingerprint/health failure restores
the exact original bytes, restarts all three units and verifies old config and
health. `CRITICAL_CONFIG_ROLLBACK_FAILED` is never reported as success.
Prepared/replaced/restarting/rollback_failed journals block another rollout;
only explicit operation-ID restore/recovery can resolve them. Duplicate apply
and already-restored operations verify processes/health without extra restart.
Backups necessarily contain secrets and must remain server-side; neither their
contents nor raw process environments/logs may be printed or copied.

Tests use an explicitly marked private temporary root and fake service boundary,
with canary secrets. No default SQLite, PostgreSQL, Redis, production process or
real model is touched by this tool's tests. The fingerprint helper reads the
literal Settings contract without importing Settings or loading workspace keys.
