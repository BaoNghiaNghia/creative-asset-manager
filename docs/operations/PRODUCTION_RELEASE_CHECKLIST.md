# Production Release Checklist

A release is **not production-ready** until the GitHub Actions check named
`Production release gate` is green for the exact commit being deployed. The
gate runs only after all existing frontend, API/worker/provider, PostgreSQL,
Elasticsearch and durable-pipeline CI groups have passed.

## Required automated evidence

- [ ] Frontend deterministic install, tests, typecheck and production build pass.
- [ ] Committed `apps/client/dist` exactly matches a source build.
- [ ] Frontend dist secret/local-URL scan passes and source maps remain absent.
- [ ] API, worker and mocked-provider unit tests pass on Python 3.12.
- [ ] PostgreSQL migration/repository integration tests pass.
- [ ] Elasticsearch v2 integration tests pass.
- [ ] Durable pipeline end-to-end tests pass with fake providers.
- [ ] Backend Docker image builds once and runs as `10001:10001`.
- [ ] Docker Compose resolves only API, worker, migrate and Elasticsearch services.
- [ ] Migration service upgrades an empty PostgreSQL 16 database to the single Alembic head.
- [ ] A container reaches native-style PostgreSQL through `host.docker.internal`.
- [ ] Production configuration rejects SQLite and loopback database hostnames.
- [ ] Persistent authentication is enabled.
- [ ] Durable RBAC is active through persistent application sessions.
- [ ] Development personal-tenant bootstrap is disabled.
- [ ] Legacy processing-admin allowlist compatibility is disabled.
- [ ] API `/live`, `/ready` and `/version` pass for the release commit.
- [ ] Worker becomes live, drains on SIGTERM and starts cleanly again.
- [ ] Native Nginx syntax, SPA fallback, static assets and `/api` proxy configuration validate.
- [ ] No credential-like value appears in the backend image history or frontend bundle.

The CI environment uses generated test-only credentials. It never reads
production secrets and does not call Google Drive, SharePoint, Gemini or
OpenAI.

## Before deployment

1. Confirm the exact commit and its checks:

   ```bash
   git rev-parse HEAD
   gh pr checks
   ```

2. Confirm the `Production release gate` is green for that SHA. A skipped,
   cancelled or neutral gate is not approval.
3. Review `deploy/production.env.example` against the VPS environment without
   printing its values.
4. Confirm the database backup and application rollback target exist.
5. Confirm Search v2, ingestion and AI flags remain disabled unless a separate
   tenant-scoped rollout was approved.

## Optional local gate

Run as a non-root user with a disposable PostgreSQL database reachable from
containers and a production-shaped environment file:

```bash
BUILD_COMMIT="$(git rev-parse HEAD)" \
CAM_BACKEND_IMAGE=cam-production-gate \
./scripts/production-release-gate.sh \
  --env-file /path/to/disposable-production-gate.env \
  --project-root "$(pwd)"
```

The environment file must use
`postgresql+psycopg://...@host.docker.internal:5432/...`; SQLite and
`127.0.0.1` database URLs are rejected. The script removes its containers
and volumes on exit unless `--keep-services` is explicitly supplied.

## Release decision

Record the commit SHA, CI run URL, gate result, migration result and operator
in the release ticket. Deploy only when every checkbox above has verifiable
green evidence. Application rollback never downgrades PostgreSQL
automatically; follow `docs/operations/VPS_PRODUCTION.md`.
