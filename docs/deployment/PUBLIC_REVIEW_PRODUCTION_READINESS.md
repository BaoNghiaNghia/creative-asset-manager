# Public Review Phase 9 production readiness

**Release candidate:** `f7a1ab95c4ab0651494f5aa4221d605ef7e087c5` (replace with the final release SHA during release).

## Scope and migration

Phases 1–9 implement bearer-link review, durable guest annotations, authenticated Review Board read/resolve controls, and audit. The database remains at Alembic head `0081_public_review_rate_limits`; Phase 9 adds no migration.

## Required remote gate

A release is production-ready only after the exact release SHA has green Frontend checks, API unit/security tests, PostgreSQL 12.22 and 16.4 integrations, Elasticsearch integration, durable pipeline E2E, Windows package, and any configured release gate. Local success is not deployment approval.

## Production configuration checklist

- `APP_ENV=production`, HTTPS `PUBLIC_APP_URL`, exact `TRUSTED_HOSTS`; no wildcard hosts.
- Empty `CORS_ALLOWED_ORIGINS` for same-origin delivery; no credentialed wildcard.
- `API_DOCS_ENABLED=false`; `AUTH_COOKIE_SECURE=true`; `AUTH_COOKIE_SAMESITE=lax`; trusted proxy CIDR only.
- Set database/OAuth/encryption/rate-limit retention values outside Git; verify feature flags, emergency stops, worker rollout and build SHA.

## Smoke and rollback

Operator sequence: backup DB; verify release SHA and env; build/pull immutable release; run `alembic upgrade head`; restart services; verify health and version; run authenticated Explorer/Review Board and signed-out public-share smoke; monitor logs. Read-only users must not resolve; resolver users must resolve/reopen idempotently; public guests must never resolve.

Rollback the application and committed frontend dist to the prior known-good SHA, restart services, and verify version/health. Do not Alembic-downgrade solely for Phase 9; restore the database only for an unrelated database incident.

## Known limitation

Review Board has no authenticated exact-source media preview contract. It fails closed with a preview-unavailable state; it does not reuse public media, provider paths, signed URLs, or credentials.
