# Committed frontend with Docker backend deployment

This deployment model builds the Vite frontend on the trusted local workstation,
commits `apps/client/dist`, and serves that artifact with native Nginx. The VPS
does not install Node.js. API, worker, and Elasticsearch run in Docker Compose;
PostgreSQL remains a native VPS service.

## Local production build

Run as the local Linux user `baonghia`:

```bash
sudo -u baonghia ./scripts/build-frontend-release.sh \
  --commit \
  --message "build(frontend): production bundle"
```

Push only when explicitly requested:

```bash
sudo -u baonghia ./scripts/build-frontend-release.sh \
  --commit \
  --push \
  --message "build(frontend): production bundle"
```

The script runs `npm ci`, frontend tests, typecheck, and the production build.
It verifies `index.html`, hashed assets, `build-meta.json`, absence of source
maps, and a bounded list of local/secret-like patterns. The marker contains only
the source commit, UTC build timestamp, and frontend package version.

`dist` is intentionally versioned so production deploys the reviewed browser
artifact without a Node toolchain. Hashed assets receive immutable caching while
`index.html` and `build-meta.json` are never cached.

## First VPS setup

1. Install Docker Engine with Compose v2, Nginx, PostgreSQL, Git, rsync, curl,
   and TLS tooling. Do not install Node.js.
2. Clone the repository to `/home/desify/creative-asset-manager` and keep it
   owned by `desify`.
3. Copy `deploy/production.env.example` to
   `/etc/creative-asset-manager/production.env`, replace every placeholder,
   set owner `root:desify`, and mode `0640`.
4. Install and customize
   `infrastructure/nginx/creative-asset-manager.conf`; replace the hostname
   and certificate paths. Enable it, run `nginx -t`, then obtain/renew TLS.
5. Create `/var/www/creative-asset-manager/releases` so `desify` can stage
   releases through the deployment script's narrowly scoped sudo operations.
6. Grant `desify` access to Docker and only the required Nginx/systemctl sudo
   commands.

All browser and API traffic is same-origin. Production frontend requests use
relative `/api` URLs. Nginx falls back to `/index.html` for routes including
`/ai-operations` and `/settings/access`.

## Native PostgreSQL

Use a dedicated, non-superuser application role and database. PostgreSQL must
listen on an address reachable through Docker's host gateway, but port 5432
must never be exposed to the public Internet.

For the fixed Compose network in this repository:

- allow only `172.29.0.0/24` in `pg_hba.conf`;
- bind PostgreSQL to loopback plus the required host/bridge-reachable address;
- restrict port 5432 with the VPS firewall;
- set `DATABASE_URL` only in the protected env file, using
  `host.docker.internal`;
- percent-encode reserved password characters.

Example shape (never commit the populated value):

```text
postgresql+psycopg://cam_app:PASSWORD@host.docker.internal:5432/creative_asset_manager
```

The deploy and validation scripts test the connection from an ephemeral API
container. They do not edit PostgreSQL configuration.

## Normal deployment

Run as `desify`:

```bash
sudo -u desify ./scripts/deploy-vps.sh \
  --branch feature/google-drive-explorer-mvp
```

The script requires a clean checkout, fetches and fast-forwards only, validates
the committed frontend, builds one immutable backend image, validates production
configuration, checks native PostgreSQL, and runs `alembic upgrade head` in a
one-shot container. A migration failure stops deployment.

Elasticsearch, API, and worker start only after those checks. The API must pass
readiness and the worker must pass its internal health check before the frontend
is copied to:

```text
/var/www/creative-asset-manager/releases/<commit>
```

The `current` symlink is switched atomically, Nginx is tested before and after
the switch, and public smoke tests cover `/`, `/ai-operations`, `/live`,
`/ready`, and `/version`. The newest five frontend releases are retained.

Run full validation after deployment:

```bash
sudo -u desify ./scripts/validate-production.sh
```

Use `--preflight` before service startup when only configuration, committed
frontend, Compose, PostgreSQL, and Alembic state should be checked.

## Rollback

```bash
sudo -u desify ./scripts/rollback-vps.sh --commit PREVIOUS_COMMIT
```

Rollback selects the matching committed frontend and backend, rebuilds the API
image, verifies readiness, then switches Nginx atomically. It preserves native
PostgreSQL and the Elasticsearch volume. It deliberately never downgrades
Alembic; verify backward schema compatibility before rolling application code
back.

## Recovery and troubleshooting

If a frontend build was forgotten, stop the deployment, return to the local
workstation, run `build-frontend-release.sh --commit`, review the bundle diff,
and push that commit explicitly. Never build on the VPS or use `git add -f`.

If database validation fails, verify PostgreSQL listening addresses,
`pg_hba.conf`, firewall rules, the Docker bridge subnet, and the protected
`DATABASE_URL`. If readiness fails, inspect sanitized Compose logs without
printing the env file. The frontend symlink is not changed until backend health
passes.
