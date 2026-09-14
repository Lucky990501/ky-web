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
Its `legacy_only` data scope blocks old-target rollback if Productized Template
rows exist; Pilot-data rollback requires a separately approved application-aware
target/evidence. Do not delete or relabel Pilot records to pass this gate.

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
