# Persistent OAuth and distributed session migration

## Current-state audit

Before Step 30, Google and Microsoft authentication used process-local
dictionaries. Access tokens, refresh tokens, profile/session data and OAuth
state/PKCE verifiers existed as plaintext Python objects. The browser cookie
contained a random opaque ID, but only the API process that created it could
resolve it. Restarting that process logged out every user; another replica
treated the same cookie as unauthenticated. OAuth callback state could not cross
replicas. Expired tokens were refreshed without a database/distributed lock, so
replicas could concurrently spend or rotate the same refresh token.

Cookies were HttpOnly and SameSite=Lax with Path=/, but Secure was separately
configured per provider and defaulted false. There was no centralized Domain or
production validation. Logout deleted process memory and the browser cookie,
but could not revoke state held by another replica. These are the risks that
Step 30 removes.

## Target storage

Revision 0013 creates PostgreSQL-authoritative oauth_connections,
auth_sessions, oauth_transactions and auth_audit_events.

- Access and refresh tokens use AES-256-GCM with a random 96-bit nonce and
  record-bound associated data. The active key version is stored, while key
  material remains in protected environment/secret-manager configuration.
- Session IDs and OAuth state values are stored only as SHA-256 digests.
- PKCE verifiers are encrypted, one-time, short-lived and browser-binding aware.
- Sessions are fixed-expiry, server-revocable, bounded and shared by all API
  replicas. Provider tokens never enter cookies.
- Refresh claims use a database atomic update and lease. Rotated refresh tokens
  and access tokens are committed together.
- Decryption failure and permanent invalid_grant fail closed and mark the
  connection reconnect_required.

## Deployment

1. Back up PostgreSQL and deploy code with PERSISTENT_AUTH_ENABLED=false.
2. Generate a 32-byte key in the deployment secret manager, for example
   openssl rand -base64 32. Configure:
   OAUTH_TOKEN_ENCRYPTION_KEYS=v1:<base64-key>
   OAUTH_ACTIVE_KEY_VERSION=v1
3. In production set APP_ENV=production and AUTH_COOKIE_SECURE=true. Review
   SameSite, Path and optional Domain for the public topology.
4. Run alembic upgrade head.
5. Enable PERSISTENT_AUTH_ENABLED=true and restart all replicas.
6. Existing in-memory sessions/tokens cannot be recovered after restart.
   Users must reconnect Google Drive and SharePoint. Do not claim otherwise.
7. Verify callback, session, logout, refresh and audit records from two replicas.
8. Schedule: python -m app.operations.auth_cli cleanup-expired.
9. Revoke a provider connection when needed:
   python -m app.operations.auth_cli revoke-connection --tenant <id> --provider google --account <provider-id> --reason <reason>

## Key rotation

1. Add the new key while retaining old keys:
   OAUTH_TOKEN_ENCRYPTION_KEYS=v1:<old>,v2:<new>
   OAUTH_ACTIVE_KEY_VERSION=v2
2. Restart replicas, then dry-run:
   python -m app.operations.auth_cli rotate-keys --dry-run --page-size 100
3. Run resumably. Save the reported cursor if execution is interrupted:
   python -m app.operations.auth_cli rotate-keys --page-size 100 --after-id <cursor>
4. Confirm no connection uses v1, then wait at least `AUTH_STATE_TTL_SECONDS`
   so every OAuth transaction encrypted with v1 has expired.
5. Remove v1 at a later deployment. Never remove an old key before all
   connection rows are rotated and the state-verifier retention window elapsed.

Progress contains counts, pages and opaque row cursors only. It never contains
tokens.

## Rollback

Disable provider login and drain API traffic. Re-enable the previous key set if
a rotation caused decryption failures. Application rollback to Step 29 requires
all users to reconnect because old processes cannot consume the PostgreSQL
session records. Downgrading to 0012 deletes persistent connections, sessions,
OAuth state and auth audit records; export non-secret audit evidence first.

## First application tenant bootstrap

Automatic personal tenant creation is off by default. For local development it
may be enabled explicitly with `DEVELOPMENT_PERSONAL_TENANT_ENABLED=true`; the
configuration is rejected in production. Repeated login reuses the same tenant
and membership and never creates a tenant per login.

Production bootstrap is an explicit operator action after the application user
has completed an approved OAuth login:

```bash
python -m app.operations.auth_cli bootstrap-tenant \
  --user-id <application-user-id> \
  --tenant-id <stable-tenant-id> \
  --name "Tenant name" \
  --slug tenant-slug \
  --reason "Initial tenant bootstrap change record" \
  --dry-run

python -m app.operations.auth_cli bootstrap-tenant \
  --user-id <application-user-id> \
  --tenant-id <stable-tenant-id> \
  --name "Tenant name" \
  --slug tenant-slug \
  --reason "Approved initial tenant bootstrap" \
  --confirm
```

The command is idempotent, requires an active application user, records a
secret-free audit event and returns only tenant/membership identifiers. It does
not accept OAuth tokens and does not assign tenant or platform administrator
roles. Role assignment belongs to the later durable RBAC migration.
