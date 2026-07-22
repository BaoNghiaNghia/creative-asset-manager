# External Asset Download Security

## Trust boundary

External URLs are untrusted input. The downloader is infrastructure code and
must not be exposed by a public route until Step 18. It is disabled by default
through `EXTERNAL_ASSET_DOWNLOADER_ENABLED`.

## Network controls

- HTTPS is mandatory and URL credentials are rejected.
- Hostnames must match an explicit exact-host or parent-domain allowlist.
- DNS is resolved and every returned address must be globally routable.
- Loopback, private, link-local, reserved, multicast, unspecified, and known
  cloud metadata addresses are rejected.
- Redirect following is manual. Every redirect repeats scheme, allowlist, DNS,
  and address validation and is subject to a maximum count.
- Connect and read timeouts are separately configurable.

## Content controls

- Responses stream into a private temporary file while enforcing a byte limit
  and calculating SHA-256.
- Image type is selected from magic bytes, never the HTTP Content-Type.
- Pillow verifies the encoded structure, dimensions and total pixel count,
  then performs a full decode.
- PNG, JPEG, GIF, WebP, TIFF and BMP signatures are accepted initially.
- Temporary files are deleted on download failure and when the async context
  manager exits, including caller exceptions.

## Logging

Only redacted URLs may be logged. Redaction removes user information, query
parameters and fragments, including signed URL credentials.

## DNS pinning

The request connects to the validated public IP while preserving the original
hostname in the HTTP Host header and TLS SNI. Environment proxy settings are
disabled so they cannot bypass the validated network destination.

## External ingestion API boundary

- The API is unavailable unless `EXTERNAL_INGESTION_API_ENABLED=true`.
- Bearer keys must be high entropy; only SHA-256 fingerprints are stored.
- Every key is bound to one tenant-owned `external_api` source.
- Database-backed per-credential rate limiting is applied before endpoint work.
- Content length and streamed bytes are both capped; validated canonical JSON is
  capped again before persistence.
- Download URLs must be HTTPS and cannot contain URL credentials or fragments.
- Validation errors omit submitted values so signed URLs are not reflected.
- Tenant/source-scoped reads return 404 for inaccessible ingestion IDs.
- Download URLs are persisted only for asynchronous workers and must never be
  written to application logs without the existing URL-redaction policy.


## Reverse-proxy and health boundary

Forwarded client and scheme headers are ignored unless `PROXY_HEADERS_ENABLED` is
set. Trusted proxies must be explicit IP addresses or CIDRs; wildcard and
all-address networks are rejected. Host validation remains independent through
`TRUSTED_HOSTS`. Health responses expose bounded state names only and never
include dependency URLs, exception messages, credentials or configuration dumps.

## Persistent OAuth security (Step 30)

OAuth connections and sessions are PostgreSQL-authoritative. Provider access and
refresh tokens are encrypted at the application boundary with versioned
AES-256-GCM keys loaded from protected configuration. Every value has a random
nonce and record/field-bound associated data. Keys are never stored in the
database. Missing keys, invalid authentication tags and unknown key versions
fail closed.

Browser cookies contain only high-entropy opaque session IDs; PostgreSQL stores
their SHA-256 digest. Sessions have fixed expiration and server-side revocation.
Production configuration refuses insecure session cookies. OAuth state is
hashed, PKCE is encrypted, browser-bound where available, short-lived and
atomically consumed once. Refresh uses a database lease so replicas do not
rotate one refresh token concurrently. Logs, audits, metrics and APIs exclude
plaintext tokens. See docs/operations/AUTH_PERSISTENCE.md for migration, key
rotation and rollback.


## Sensitive URL lifecycle and retention (Step 32)

Signed external URLs use a dedicated versioned AES-256-GCM key ring. Associated
data binds ciphertext to tenant and ingestion item. Request history omits URL
values, and processing jobs store only stable ingestion/item IDs. Tenant-scoped
repository resolution decrypts immediately before use. Query parameters are
removed before errors become durable or logs are emitted.

Expired or consumed URL ciphertext is tombstoned by bounded retention cleanup.
Completed payloads and raw provider/AI responses follow explicit central
retention settings. Dead-letter cleanup retains status and error classification
while removing payloads and detailed messages. Cleanup logs expose counts and
record types only. Authoritative asset identity and append-only audit data are
not cleanup targets.

## Tenant RBAC boundary (AUTH-03)

Tenant authorization is derived only from active application users, active
tenants, active memberships and durable role assignments. It is never inferred
from email domains, Drive ownership, OAuth scopes or provider account type.
Stable permission keys are global catalog data; role instances and assignments
are tenant-scoped. Composite database foreign keys prevent a role from one
tenant being assigned to a membership in another tenant.

System roles are protected templates instantiated per tenant. Tenant
administrators receive tenant permissions only; platform administration cannot
be granted through tenant role data or the tenant RBAC seed command.

## Central authorization boundary (AUTH-04)

`CurrentPrincipal` is the single application identity context for new protected
routes. It validates the opaque persistent session, active application user,
active tenant membership and effective tenant permissions before returning a
principal. Tenant authorization never replaces repository tenant predicates;
services and repositories must continue accepting an explicit tenant ID.

Platform administration is a durable assignment in
`platform_admin_assignments`, separate from tenant roles. The principal carries
only a SHA-256 session identifier internally; neither it nor OAuth/provider
credentials are returned by `/api/v1/auth/identity`. Authorization failures use
stable `authentication_required`, `user_disabled`,
`tenant_membership_required`, `permission_required` and `tenant_mismatch`
codes without exposing role storage details.

## OAuth application login boundary (AUTH-05)

Google and Microsoft subjects, not email addresses, are the authoritative
external identities. An OAuth callback resolves `provider + provider_subject`,
updates only bounded profile fields and never links identities merely because
their email addresses match. Provider access/refresh credentials remain in the
encrypted OAuth connection record and never enter the application session.

First-login admission is fail-closed. `AUTH_SELF_SIGNUP_ENABLED` defaults to
false; an optional normalized domain allowlist is admission policy only and
never grants tenant or platform roles. Approved signup requires an explicit
active default tenant, except for the separately guarded local-development
personal-tenant flow. No login path assigns an administrator role.

New application sessions contain the durable user and active tenant IDs, use a
fresh opaque session ID and validate active user, tenant and membership state.
Tenant switching validates membership, rotates the session, revokes the old
session and records a secret-free audit event. Legacy actor-only sessions are
accepted only before an explicitly configured ISO-8601 compatibility deadline.

`PROCESSING_POLICY_ADMIN_IDS` is deprecated. Its bridge to platform privilege
is inactive unless `AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED=true`; the
flag defaults to false in every environment example. Existing admin routes are
not bulk-migrated in AUTH-04. They retain their compatibility dependency until
the route-by-route RBAC migration step.

## Tenant access administration (AUTH-06)

Membership and role APIs require both a validated `CurrentPrincipal` and the
specific tenant permission. Tenant path scope is checked independently and all
repository/service queries retain an explicit tenant predicate. UI visibility
is not an authorization control.

Invitation without an email provider is a persisted `invited` membership for
an existing, unambiguous application user; the system does not fabricate email
delivery or link same-email external identities. Platform administration is
not a permission or tenant role and cannot be granted through these APIs.

Mutations serialize on the tenant row before checking the final-administrator
invariant. Removing/suspending the last active `tenant_admin` or removing its
role is rejected. Only a durable platform administrator with an explicit
override may perform recovery. Custom-role grants are limited to permissions
the actor already holds. System roles stay protected, removals preserve
membership history, and all successful mutations create bounded, secret-free
audit events with actor and reason.
