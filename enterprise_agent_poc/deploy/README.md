# Gate 3.3 deployment

The production stack is Docker Compose based and intentionally has no exposed
PostgreSQL, Redis, or Platform MCP ports. The host Nginx terminates TLS and
proxies to the loopback-only Compose Nginx listener (`127.0.0.1:18080`).

## Server preparation

1. Clone the approved Git commit into a dedicated release directory; do not
   reuse the release root or Nginx site of the existing `ky_web` website.
2. Copy `.env.production.example` to `.env.production` on the server, set the
   database/Redis/session secrets, and inject DeepSeek, gateway and OSS keys
   from the server secret store. The file must be mode `0600` and never enter
   Git.
3. Run `docker compose --env-file .env.production up -d --build`.
4. Configure a dedicated HTTPS Nginx virtual host that proxies only to
   `http://127.0.0.1:18080`. Platform MCP remains on the Docker network.
5. Verify `/api/v1/poc/health`, database migration, Redis worker logs and an
   authenticated browser task before directing traffic to the new host.

## Runtime configuration verification

The release environment file is the authoritative source for one release. Do
not separately inject any of the RAG/database/embedding variables through a
systemd unit, shell profile, or an old deployment directory. Before a RAG
acceptance run, execute the verifier in a process that inherited the same
environment as the API service:

```powershell
python scripts/verify_runtime_config.py --environment-file .env.production
```

It prints only presence flags and SHA-256 fingerprints, never values or
secrets. `matches` must be `true`. For an already-running API, compare its
admin-only `/api/admin/runtime/diagnostics` `runtime_config.fingerprint` with
the verifier's `expected_fingerprint`; a mismatch blocks the acceptance run.

The existing workspace server settings are a connection reference only. They
must not be copied into this project or used to overwrite the existing site.
