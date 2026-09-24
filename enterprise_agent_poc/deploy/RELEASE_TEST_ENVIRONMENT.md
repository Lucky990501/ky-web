# Linux Release Test Environment Contract

This contract is for an isolated release-test checkout, not a Production
service or application dependency. The sole version/checksum source is
[`release_test_environment.json`](release_test_environment.json). A Node or
Redis version change requires a separate controlled change to that file and
its contract tests.

## Prepare the native tools

Use the Linux Python 3.11 project environment:

```bash
cd enterprise_agent_poc
.venv/bin/python scripts/release_test_environment.py prepare
```

The script downloads the official Linux x64 Node archive and official Redis
source over HTTPS, compares their SHA-256 digests to the pinned values, then
installs/builds **only** under `~/.cache/enterprise-agent-test-runtime/`.
It never uses an unverified download, a Windows `node.exe`, or a system-default
Node. The exact Node binary directory is prepended to child `PATH` by the
runner. Redis is compiled from the exact-version verified source; no system
Redis version is accepted as a substitute.

## Redis E2E isolation and manifest

[`release_test_redis_manifest.json`](release_test_redis_manifest.json) is the
repo-owned template for the **existing** `stage2_isolation.py` manifest schema.
The runner resolves it into a private, mode-0700 file inside a fresh
`/private/tmp/ky-web-stage2-readiness.*` root. The generated manifest is never
committed. It contains only local test configuration and a **path** to the
non-Production credential file, never the credential value.

The root has the required ownership and `stage2-isolated.marker`. PostgreSQL
16.6 uses its own `pg/cluster`, private `pg/socket`, UTF8/C database named
`stage25_*`, and Unix-socket port 54330 with TCP disabled. Redis uses its own
`redis.sock`, database 0, namespace equal to the unique root name, and TCP
disabled. Both socket directories are owner-only. The runner invokes the
existing `stage2_preview.py check` gate before provisioning anything.

`scripts/release_test_environment.py run` owns the complete service lifecycle:

1. Prepare/verify Node and Redis versions.
2. Create a marked private root and start isolated PostgreSQL and Redis.
3. Verify Redis `PING`, `INFO server` exact version, and Stage 2 isolation.
4. Apply all current ordered PostgreSQL migrations through the formal
   `scripts/migrate.py` runner. Initialize only empty system tables, then run
   `scripts/compatibility_epoch.py bootstrap-current --config <private manifest>`.
   This command requires the marked private PostgreSQL/Redis fixture, exact
   current migrations and data-contract floor, zero business rows, and a clean
   committed source. It never accepts a caller-selected epoch and does not
   replace the historical `advance` path. After it succeeds, provision the
   synthetic tenants/user, bootstrap the published `social-content-agent` and
   two public completed tasks through the normal services, then start isolated
   MCP, API, and Worker.
5. Supply `STAGE25_REDIS_E2E_CONFIG` and `STAGE25_API_PID` to pytest.
6. Stop the three application processes, Redis, and PostgreSQL, then delete
   only the marked root created by this invocation, also when pytest fails.

The non-Production provider credential must already be in a separate regular
file with mode 0600 and a `STAGE2_DEEPSEEK_TEST_API_KEY=...` entry. Do not put
that value in Git, the manifest, a Candidate, or the command line. Example:

```bash
.venv/bin/python scripts/release_test_environment.py run \
  --credential-file /absolute/private/test-credential-file -- \
  -q -ra
```

For one release-critical test, replace `-q -ra` with its test path. An absent
credential is a hard error; it must not be replaced with a dummy value to
make the E2E appear to pass. Stage-1 PostgreSQL and historical-artifact fixture
roots remain separate caller-supplied prerequisites via
`STAGE1_POSTGRES_ROOT` and `ROLLBACK_OLD_ARTIFACT_ROOT`.

The tool rejects non-Linux-x64 hosts. It does not connect to Production Redis,
read Production registry data, change `release-current`, start systemd services,
or modify Nginx. Its private Unix socket and database identity are checked by
`scripts/stage2_isolation.py`; a network/Production Redis URL is rejected before
any database initialization.
