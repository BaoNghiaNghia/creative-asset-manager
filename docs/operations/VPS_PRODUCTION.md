# VPS production deployment

This runbook covers PROD-VPS-03 only. The target is a hybrid VPS deployment:

- native Nginx terminates TLS and serves the committed frontend;
- native PostgreSQL listens on VPS loopback;
- Docker Compose runs Elasticsearch, API, worker and the one-shot migration service;
- user `desify` owns the checkout at `/home/desify/creative-asset-manager`.

No command in this document deploys automatically.

## Filesystem layout

```text
/home/desify/creative-asset-manager/
/var/www/creative-asset-manager/
  releases/<full-git-commit>/
  current -> releases/<full-git-commit>
/etc/creative-asset-manager/production.env
```

The frontend in each release is copied from the committed
`apps/client/dist`. The release directory is immutable after creation. Nginx
serves only `/var/www/creative-asset-manager/current`.

## One-time host setup

Install Git, Docker Engine with Compose v2, Nginx, rsync and curl. Add
`desify` to the Docker group and grant only the required passwordless sudo
commands for Nginx validation/reload and frontend release installation. Keep
PostgreSQL bound to `127.0.0.1:5432`; firewall ports 5432, 8000, 8081 and
9200 from external access.

Install the Nginx file after replacing the hostname and TLS certificate paths:

```bash
sudo install -o root -g root -m 0644 \
  infrastructure/nginx/creative-asset-manager.conf \
  /etc/nginx/sites-available/creative-asset-manager.conf
sudo ln -s /etc/nginx/sites-available/creative-asset-manager.conf \
  /etc/nginx/sites-enabled/creative-asset-manager.conf
sudo nginx -t
sudo systemctl reload nginx
```

Nginx forwards API traffic only to `127.0.0.1:8000`, replaces forwarded
client headers with values it derives itself, serves SPA deep links such as
`/ai-operations` and `/settings/access`, denies dot/environment files, and
uses immutable caching only for hashed assets.

## Production environment

Copy `deploy/production.env.example` to
`/etc/creative-asset-manager/production.env`, replace every placeholder and
set mode 0600 or 0640. Important invariants:

- `APP_ENV=production`;
- `PUBLIC_APP_URL` is HTTPS;
- `DATABASE_URL` uses native PostgreSQL through
  `host.docker.internal:5432`;
- `AUTH_COOKIE_SECURE=true`;
- persistent authentication is enabled;
- development tenant and legacy admin bypasses are disabled;
- `API_DOCS_ENABLED=false`.

Validate without displaying values:

```bash
sudo -u desify ./scripts/validate-production.sh --config-only
```

## Deploy

Deploy a branch with fast-forward-only semantics:

```bash
sudo -u desify ./scripts/deploy-vps.sh --branch main
```

Or deploy a fetched commit:

```bash
sudo -u desify ./scripts/deploy-vps.sh --commit FULL_GIT_COMMIT
```

The deployment fails closed when run as root or a user other than `desify`.
For a deliberately different service account, pass
`--allow-user "$(id -un)"`; this is explicit and is recorded in terminal
output.

The script performs these operations in order:

1. validates the environment and clean checkout without printing secrets;
2. fetches Git and moves only by fast-forward, or selects the requested commit;
3. verifies and scans the committed frontend;
4. builds one backend image tagged with the full Git commit;
5. tests native PostgreSQL from that container;
6. runs `alembic upgrade head` through the one-shot migration service;
7. starts Elasticsearch, API and worker;
8. waits for `/live`, `/ready`, worker liveness and a matching `/version.commit`;
9. stages the frontend and atomically changes the `current` symlink;
10. validates and reloads Nginx;
11. smoke-tests `/`, `/ai-operations`, `/settings/access`, `/live`,
    `/ready` and the matching `/version`;
12. retains the five newest frontend releases.

## Validate a running release

```bash
sudo -u desify ./scripts/validate-production.sh
```

Use `--preflight` to stop after Compose, application configuration,
PostgreSQL and Alembic-head checks.

## Rollback

Use a retained commit explicitly:

```bash
sudo -u desify ./scripts/rollback-vps.sh --commit FULL_GIT_COMMIT
```

Without `--commit`, the script selects the newest retained release other than
the current symlink. Rollback builds and starts the backend image tagged with
that same commit, waits for matching health/version, then atomically switches
the frontend and reloads Nginx.

Rollback never runs `alembic downgrade`. PostgreSQL stays at the current
schema revision. Confirm the older application is backward-compatible with the
current schema before rollback. Elasticsearch and PostgreSQL data are
preserved.

## Failure and recovery

Before the frontend switch, a failed backend, database, migration or health
check leaves the existing frontend active. After a switch, a failed smoke test
restores the previous frontend symlink. The database is never downgraded and
the scripts never print environment values.

Safe diagnostics:

```bash
docker compose --env-file /etc/creative-asset-manager/production.env \
  -f infrastructure/docker/docker-compose.prod.yml ps
curl --fail --silent http://127.0.0.1:8000/live
curl --fail --silent http://127.0.0.1:8000/version
sudo nginx -t
```

Do not print the production environment, provider credentials, OAuth tokens or
signed URLs while troubleshooting.
