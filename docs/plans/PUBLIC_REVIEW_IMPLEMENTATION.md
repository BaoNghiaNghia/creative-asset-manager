# Public Review Portal Implementation Plan

**Status:** APPROVED IMPLEMENTATION PLAN  
**Architecture source of truth:** `docs/architecture/PUBLIC_REVIEW.md`  
**Execution rules:** `docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`  
**Last updated:** 2026-09-17

This plan defines the implementation sequence for the CAM Public Review Portal. Each phase is intentionally bounded. A phase is complete only after its required tests pass and its security-sensitive completion report is recorded. Do not collapse all phases into one broad change.

## 0. Mandatory preflight before every phase

Before editing code:

1. Read `AGENTS.md`.
2. Read `docs/security/SECURITY_GOVERNANCE.md`.
3. Read `docs/architecture/AGENT.md`.
4. Read `docs/architecture/SECURITY.md`.
5. Read `docs/architecture/DATA_MODEL.md` when the phase touches persistence.
6. Read `docs/architecture/PUBLIC_REVIEW.md` in full.
7. Read this plan and `PUBLIC_REVIEW_CODEX_RUNBOOK.md`.
8. Inspect the actual current implementation and tests for the modules being changed.
9. Record current `main`/branch SHA and baseline test failures before attributing any failure to the feature.
10. Do not weaken existing security controls or tests to make the phase pass.

Implementation should normally occur on a feature branch created from current `main`, with one focused commit or small logical commits per phase. Production deploy is not part of these phases.

---

# Phase 1 — Durable data foundation

## Objective

Introduce the PostgreSQL-authoritative Public Review persistence model without exposing public HTTP routes or frontend behavior.

## Expected module boundary

Create a dedicated backend module consistent with current project conventions, for example:

```text
apps/api/app/modules/public_review/
  __init__.py
  model.py
  repository.py
  schema.py
  service.py
```

Exact filenames may differ when current conventions justify it, but Public Review should remain an identifiable module.

## Required entities

Implement the data model defined by `docs/architecture/PUBLIC_REVIEW.md`:

- `public_shares`;
- `public_share_scopes`;
- `public_share_sessions`;
- `public_share_guests`;
- `asset_annotations`.

## Migration requirements

Create one new Alembic migration on top of the then-current single head.

The migration must:

- be additive/non-destructive;
- preserve one head;
- use tenant-enforcing constraints where practical;
- include indexes/uniqueness required by the architecture contract;
- support PostgreSQL integration CI;
- preserve SQLite local/test compatibility where repository support remains active;
- avoid unsupported direct SQLite `ALTER COLUMN` behavior;
- provide downgrade logic limited to the newly introduced feature tables/constraints.

Do not edit historical migrations.

## Repository/service requirements

Implement bounded repository operations required by later phases, including at minimum:

- create/get/list/update/revoke share;
- lookup by public ID;
- lookup/verification support for secret digest;
- replace/list share scopes;
- create/revoke/resolve public share sessions;
- create/update guest identity;
- create/list/update/delete/resolve annotations;
- tenant-explicit predicates on all tenant-owned reads/writes.

Do not add public routes yet.

## Required tests

At minimum:

- tenant isolation for every entity;
- duplicate scope prevention;
- raw share secret never persisted;
- raw session token never persisted;
- revoked/expired state helpers fail as expected;
- session expiry cannot outlive the share according to service logic;
- annotation tenant/share/asset/source ownership constraints;
- invalid normalized anchor coordinates rejected;
- reply ownership/thread relationship validation;
- migration one-head check;
- PostgreSQL upgrade/downgrade/upgrade path;
- SQLite compatibility tests relevant to new models/migration.

## Acceptance gate

Phase 1 is complete only when:

- schema exists only through the new migration;
- no public route exists;
- no frontend dependency changed;
- no raw secret/session value is stored;
- relevant unit and migration tests pass;
- mandatory completion report is produced.

## Recommended commit

```text
feat(public-review): add durable share and annotation models
```

---

# Phase 2 — Share authorization and folder-scope engine

## Objective

Create the server-side authorization boundary for public shares while reusing/refactoring existing viewer folder ancestry logic rather than duplicating it.

## Required investigation

Inspect in detail:

```text
apps/api/app/modules/authorization/folder_scope.py
apps/api/app/modules/authorization/folder_scope_cache.py
apps/api/app/modules/authorization/principal.py
apps/api/app/modules/explorer/
apps/api/app/modules/assets/
```

and their tests.

## Required design

Refactor only enough to expose a reusable lower-level folder hierarchy/scope resolver used by both:

```text
ViewerFolderScopeService
PublicShareScopeService
```

The shared resolver must preserve fail-closed behavior and exact source identity.

Introduce a dedicated `SharePrincipal` or equivalent public authorization context. It must not subclass/fabricate a CAM user membership merely for convenience.

## Required behavior

- resolve current share from server-backed public share session;
- verify active/not revoked/not expired on every protected public request;
- selected roots and descendants allowed;
- siblings/unselected roots denied;
- missing hierarchy data denied;
- exact asset/source pair preserved;
- provider IDs cannot masquerade as internal asset IDs;
- canonical asset linked to an unauthorized source remains unauthorized through that source;
- authorization failure does not disclose foreign object existence.

## Required negative tests

At minimum:

```text
share_A -> tenant_B asset                         denied
share_A -> unselected root                        denied
share_A -> sibling folder                         denied
share_A -> selected descendant                    allowed
share_A -> allowed canonical asset/wrong source   denied
missing hierarchy data                            denied
provider ID used as internal asset ID             denied
revoked share/session                             denied
expired share/session                             denied
unknown public share ID                           denied
```

Existing viewer-folder tests must remain passing.

## Acceptance gate

No browser-facing public asset route is required yet. Phase 2 is complete when the service-level authorization boundary is independently tested and existing viewer behavior is unchanged.

## Recommended commit

```text
feat(public-review): add scoped public share authorization
```

---

# Phase 3 — Authenticated share-management API

## Objective

Let authorized internal CAM users create/manage public review links.

## RBAC

Introduce/reuse the dedicated tenant permission:

```text
public_review.manage
```

Internal management routes use `CurrentPrincipal`, active tenant context, explicit tenant predicates, and existing RBAC/audit infrastructure.

## Functional route contract

Implement the functional equivalent of:

```text
POST   /api/v1/public-review/shares
GET    /api/v1/public-review/shares
GET    /api/v1/public-review/shares/{share_id}
PATCH  /api/v1/public-review/shares/{share_id}
DELETE /api/v1/public-review/shares/{share_id}
POST   /api/v1/public-review/shares/{share_id}/rotate-secret
```

`DELETE` means revoke, not destructive database deletion.

## Create/rotate secret behavior

Create a cryptographically strong secret. Persist SHA-256 digest only.

Return a complete share URL only during create or rotate:

```text
/share/<public_id>#key=<raw-secret>
```

Do not expose the secret in normal subsequent reads.

Rotation must invalidate the old secret and active share sessions according to the architecture contract.

## Scope validation

Management API must validate:

- source belongs to active tenant;
- selected folder belongs to that source where current provider/local registry can prove it;
- duplicate scopes rejected/deduplicated safely;
- one share cannot cross tenants.

## Audit

Record bounded, secret-free evidence for:

- create;
- settings update;
- scope update;
- rotate;
- revoke.

## Required tests

- `public_review.manage` required;
- tenant mismatch denied;
- source from another tenant denied;
- create returns raw secret once while DB contains only digest;
- list/get never returns secret;
- rotate invalidates old secret;
- revoke invalidates access/session path;
- invalid expiry rejected;
- scope uniqueness;
- audit contains no raw secret or provider credential.

## Acceptance gate

No public Asset Explorer UI is required. An authorized CAM user can safely configure shares through authenticated API only.

## Recommended commit

```text
feat(public-review): add share management API
```

---

# Phase 4 — Public session exchange and read-only explorer API

## Objective

Create the first internet-facing Public Review surface: secret exchange, share-session cookie, scoped folder browsing, asset metadata, thumbnails, and previews.

## Session exchange

Implement:

```text
POST /api/public/review/{public_share_id}/session
```

Input is the secret obtained from the URL fragment. The server validates the digest and issues an opaque HttpOnly share-session cookie. Raw key and cookie values must never be persisted or logged.

Session rotation/revocation behavior must match `PUBLIC_REVIEW.md`.

## Read API

Implement functional equivalents of:

```text
GET /api/public/review/{public_share_id}/bootstrap
GET /api/public/review/{public_share_id}/folders
GET /api/public/review/{public_share_id}/folders/{folder_id}/children
GET /api/public/review/{public_share_id}/assets/{asset_id}
GET /api/public/review/{public_share_id}/assets/{asset_id}/thumbnail
GET /api/public/review/{public_share_id}/assets/{asset_id}/preview
```

Do not expose a generic arbitrary proxy route.

## Response minimization

Public responses must exclude:

- tenant internals not required by UI;
- membership/role state;
- OAuth/provider credentials;
- provider signed URLs;
- local paths;
- hidden root/source metadata;
- internal error details.

## Cache/security headers

Implement public-surface response headers consistent with architecture:

- secret/session/bootstrap state uses `no-store`;
- `Referrer-Policy` on the public application should be strict;
- media caching cannot become a durable authorization bypass;
- revocation behavior must be tested.

## Rate limiting

Add edge/application controls as defined in `PUBLIC_REVIEW.md`. The implementation must document whether application rate-limit state is single-process, shared, or PostgreSQL-backed and ensure it matches actual deployment topology.

## Required tests

- fragment secret is not part of server route contract;
- valid exchange creates opaque session;
- wrong key denied generically;
- selected root/descendant allowed;
- sibling/unselected root denied;
- cross-tenant denied;
- wrong source for same canonical asset denied;
- thumbnail cannot bypass authorization;
- preview cannot bypass authorization;
- expired/revoked share denied;
- rotated old share session/key denied;
- public response does not include provider credential fields;
- rate limit behavior;
- cache/revocation regression tests where practical.

## Acceptance gate

The backend supports a safe read-only public review experience before any comment writes are enabled.

## Recommended commit

```text
feat(public-review): expose scoped public asset browsing
```

---

# Phase 5 — Guest identity and annotation backend

## Objective

Allow public reviewers to identify with a display name and create/edit review annotations while preserving share/asset/source authorization.

## Guest behavior

Implement functional endpoint:

```text
POST /api/public/review/{public_share_id}/guest
```

The endpoint associates a bounded guest identity with the current valid share session. It does not create a CAM user or membership.

## Annotation API

Implement functional equivalents of:

```text
GET    /api/public/review/{public_share_id}/assets/{asset_id}/annotations
POST   /api/public/review/{public_share_id}/assets/{asset_id}/annotations
PATCH  /api/public/review/{public_share_id}/annotations/{annotation_id}
DELETE /api/public/review/{public_share_id}/annotations/{annotation_id}
```

## Ownership rules

- annotation reads require current share authorization;
- writes require `allow_comments=true`;
- writes require associated guest identity;
- public guests cannot resolve or reopen annotations; those authenticated controls are reserved for the Review Board phase;
- guest may edit/delete only its own annotation in MVP;
- replies must belong to same share and same asset/source thread;
- cross-share IDs return generic denial/not-found;
- revoking share stops subsequent reads/writes.

## Rich-text backend validation

Implement the backend content contract before introducing Tiptap UI. Validate:

- encoded size;
- text size;
- depth;
- node count;
- allowed node/mark types;
- link protocol;
- structure.

Derive `plain_text` server-side.

A temporary plain-text client may still submit a valid paragraph JSON document.

## CSRF/origin

Because writes use cookies, implement the approved origin/CSRF policy before enabling annotation mutations. Do not rely on SameSite alone.

## Required tests

- read comments without guest identity when share is valid;
- write requires guest;
- `allow_comments=false` blocks writes;
- guest A cannot edit/delete guest B content;
- outside-scope asset denied;
- cross-share annotation denied;
- cross-tenant denied;
- reply to different asset/source denied;
- invalid/partial anchor rejected;
- oversized/deep/excess-node document rejected;
- unsupported node rejected;
- dangerous URL scheme rejected;
- server-derived plain text ignores client spoof;
- invalid Origin/CSRF case rejected;
- rate limiting on write path;
- provider/source metadata unchanged.

## Acceptance gate

Annotation backend is secure and independently tested before rich frontend behavior is added.

## Recommended commit

```text
feat(public-review): add scoped asset annotations
```

---

# Phase 6 — Public Review frontend MVP

## Objective

Build a separate public frontend route that consumes only the public API and offers an Asset Explorer-like browsing/review experience.

## Feature boundary

Create:

```text
apps/client/app/public-review/
```

with components/modules equivalent to those listed in `PUBLIC_REVIEW.md`.

The existing large `App.tsx` should receive minimal route wiring only.

## Public route

```text
/share/:publicShareId
```

On initial load:

1. frontend reads `#key=` from fragment if present;
2. exchanges it for share session;
3. removes the fragment from visible URL/history as soon as practical;
4. loads bootstrap/folders using cookie-backed public API.

Never persist the raw share key in localStorage/sessionStorage/IndexedDB.

## Required UI

- share header/title;
- folder sidebar/tree;
- asset grid;
- annotation-count badge when available;
- image viewer/lightbox;
- review sidebar;
- no mandatory guest-name dialog;
- temporary plain-text composer through a stable `AnnotationEditor` interface;
- previous/next keyboard navigation;
- loading/empty/error/revoked/expired/comments-disabled states;
- responsive and accessible controls.

## Reuse rules

Allowed reuse:

- presentation primitives;
- safe image/media components;
- formatters/layout patterns.

Forbidden implicit reuse:

- authenticated API hooks;
- CurrentPrincipal assumptions;
- internal navigation/sidebar;
- client-side permission state as an authorization mechanism.

## Required tests

- fragment exchange then fragment removed;
- public route does not require CAM login;
- no internal navigation shown;
- only API-returned scoped folders/assets rendered;
- invalid/revoked/expired states;
- viewer navigation;
- comments-disabled state;
- anonymous session guest flow;
- create annotation flow;
- edit-own affordance only;
- raw HTML not rendered;
- no localStorage persistence of share secret.

## Required frontend checks

```text
npm test
npm run typecheck
npm run build
```

Any committed-dist workflow used by the repository must also be satisfied according to current CI conventions.

## Acceptance gate

A reviewer can open a share link, browse allowed assets, identify, and create basic comments through the public UI.

## Recommended commit

```text
feat(public-review): add public review portal
```

---

# Phase 7 — Notion-style Tiptap editor and image pins

## Objective

Upgrade the annotation composer to the approved rich-text interaction model and enable normalized point annotations on images.

## Dependency rule

Before adding packages, inspect current package policy and lockfile. Add only the minimum Tiptap dependencies needed by the allowed schema.

Likely dependencies include:

```text
@tiptap/react
@tiptap/starter-kit
@tiptap/extension-link
@tiptap/extension-placeholder
@tiptap/extension-task-list
@tiptap/extension-task-item
```

Do not add Yjs or realtime collaboration dependencies.

## Editor behavior

Implement the approved supported nodes/marks and a Notion-like slash command menu:

```text
Text
Heading 1
Heading 2
Heading 3
Bulleted list
Numbered list
To-do
Quote
Divider
```

Store JSON document only; generated HTML is presentation output, not source of truth.

## Pin behavior

- explicit Add Pin mode;
- click inside actual rendered image content rectangle;
- calculate normalized x/y;
- responsive resize preserves position;
- pins map to annotation/thread;
- selecting pin selects comment;
- deleted annotation removes pin;
- non-pinned comments remain supported;
- zoom/contain/letterbox behavior does not distort coordinates.

Coordinate math should be extracted into independently testable pure utilities where practical.

## Required tests

- editor JSON round trip;
- slash command basics;
- safe link handling;
- unsupported/raw HTML behavior blocked;
- normalized coordinate calculation;
- contain/letterbox coordinate calculation;
- coordinates stable after resize;
- pin selects thread;
- non-pinned comments unaffected.

## Required frontend checks

```text
npm ci
npm test
npm run typecheck
npm run build
```

Commit lockfile changes.

## Acceptance gate

Public comments now have the approved Notion-like editing experience and stable image-point annotations without realtime collaboration complexity.

## Recommended commit

```text
feat(public-review): add rich review annotations
```

---

# Phase 8 — Asset Explorer integration, management UI, hardening

## Objective

Expose share creation/management in authenticated CAM and perform full security/regression hardening before production consideration.

## Internal UI

Add `Share for review` to the appropriate eligible Asset Explorer folder action/context menu.

Create management UI for:

- review name;
- current/additional folder scopes;
- allow comments;
- allow download policy;
- expiration;
- create/copy link;
- list existing shares;
- edit settings/scopes;
- rotate secret with confirmation;
- revoke with confirmation.

UI visibility requires `public_review.manage`, but server enforcement remains authoritative.

A previously issued secret must never be shown again. Only create/rotate responses can expose a complete URL.

## Final hardening matrix

The following must be exercised end-to-end or at the highest practical integration level:

1. valid share browses selected root and descendants;
2. sibling cannot be enumerated;
3. foreign tenant cannot be enumerated;
4. direct asset URL cannot bypass scope;
5. thumbnail cannot bypass scope;
6. preview cannot bypass scope;
7. annotations cannot bypass scope;
8. same canonical asset through unauthorized source denied;
9. revocation invalidates existing sessions/API/media;
10. rotation invalidates old key and sessions;
11. expiration invalidates access;
12. comments-disabled share cannot mutate;
13. guest A cannot mutate guest B annotation;
14. public principal cannot call internal management routes;
15. internal user without permission cannot manage shares;
16. secrets absent from logs/audits/responses/persistence where prohibited;
17. malicious rich-text input safely rejected;
18. rate limits enforced;
19. existing ViewerFolderScope regression suite remains green;
20. public review cannot mutate source/provider asset metadata.

## Documentation updates

When implementation reality intentionally differs from this approved design, update the canonical docs in the same reviewed change. Do not silently diverge.

Update `ROADMAP.md` and `REVIEW.md` according to existing repository agent instructions after completion.

## Broad test set

At minimum run relevant current equivalents of:

```text
Frontend unit tests
Frontend typecheck
Frontend production build
API unit tests
Authorization tests
Public Review tests
Alembic single-head check
PostgreSQL migration/repository integration
SQLite compatibility tests where supported
```

Run Elasticsearch/pipeline suites if shared code changed in a way that can affect them.

## Production gate

Phase 8 completion does not authorize production deployment.

Before production deployment, require:

- green relevant CI;
- human review of auth/session/migration/public-media boundaries;
- migration review;
- configured HTTPS/cookie/security headers;
- reverse-proxy public-route rate limits;
- production feature flag/config verification;
- rollback readiness;
- explicit deployment authorization.

## Recommended commit

```text
feat(public-review): integrate review sharing into asset explorer
```

---

# Post-phase independent review gate

After each phase, before beginning the next phase, perform a separate review pass that does not add new product features.

The review must specifically inspect for:

```text
tenant escape
folder-scope bypass
asset/source identity confusion
provider ID vs internal asset ID confusion
raw secret/session leakage
provider credential leakage
frontend-only authorization
missing tenant predicates
fail-open hierarchy behavior
revocation/cache problems
unsafe migration behavior
CSRF/origin gaps
rich-text injection
rate-limit bypass
missing negative tests
```

Concrete regressions discovered during the review should be fixed within the current phase before moving on.

# Mandatory completion report for every phase

Every phase report must contain:

```text
Change summary
Files changed
Behavior changed
Security boundary affected
Invariants affected
Data migration? yes/no
New dependency? yes/no
Secrets/credentials impact
Production permissions required
Tests added/updated
Tests actually run and exact results
Known risks
Rollback procedure
Current commit SHA
Next phase readiness
```

Do not state that tests passed unless they were actually run.

# Change-control rule

`docs/architecture/PUBLIC_REVIEW.md` is the design authority. This implementation plan defines sequencing, not permission to reinterpret the architecture. If implementation uncovers a reason to change a security or identity contract, stop that phase, document the proposed architectural change, review it, update the docs, and only then implement the changed contract.
