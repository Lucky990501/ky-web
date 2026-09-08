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

The existing workspace server settings are a connection reference only. They
must not be copied into this project or used to overwrite the existing site.
