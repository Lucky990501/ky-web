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
3. Run candidate `scripts/migrate.py status`; reject mismatches and unknowns.
4. Verify runtime dependency health and the shared configuration fingerprint.
5. Run `deploy/release_switch.sh <release-id>` as root.
6. Require API, MCP, and Worker to be active and `/api/health` to report
   `status=ok`, `knowledge=ok`, and `environment=production`.
7. Complete authenticated browser acceptance. The switch script automatically
   restores the previous Release if its controlled health gate fails.

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
