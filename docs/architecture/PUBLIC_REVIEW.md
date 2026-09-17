# Public Review Portal Architecture

**Status:** APPROVED DESIGN — NOT YET IMPLEMENTED  
**Applies to:** public asset sharing, review links, public folder browsing, guest review identity, asset annotations, media delivery, rich-text review UI, internal share management  
**Primary branch:** `main`  
**Last updated:** 2026-09-17

This document is the canonical architecture and security contract for the Creative Asset Manager (CAM) Public Review Portal. Future implementation phases must preserve this contract unless a later reviewed documentation change explicitly replaces part of it.

The Public Review Portal lets an authenticated CAM user create a public review link for one or more explicitly selected folders. A person opening that link can browse only those folder roots and their descendants, preview allowed assets, and add review annotations without receiving an internal CAM account. The public experience is intentionally separate from the authenticated Asset Explorer even when it reuses lower-level asset, provider, preview, and folder-hierarchy services.

Before changing this area, agents must also read:

- `AGENTS.md`
- `docs/security/SECURITY_GOVERNANCE.md`
- `docs/architecture/AGENT.md`
- `docs/architecture/SECURITY.md`
- `docs/architecture/DATA_MODEL.md`
- `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`
- `docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`

## 1. Goals

The approved MVP must support:

1. An authenticated CAM principal with the required permission can create a review share for one or more provider folders.
2. A share exposes only explicitly selected roots and descendants; siblings, other roots, other sources, and other tenants remain undiscoverable.
3. Public reviewers do not need a CAM account.
4. A public reviewer can browse folders and assets using an experience derived from Asset Explorer without gaining access to authenticated CAM routes or state.
5. Public reviewers can add rich-text comments to an asset and, later in the MVP sequence, anchor comments to normalized points on the rendered image.
6. Reviews are stored in CAM/PostgreSQL and never mutate Google Drive, SharePoint, or another source provider.
7. Share owners can revoke, rotate, expire, and update share settings.
8. Every public media request repeats authorization server-side.
9. Share secrets, provider credentials, signed URLs, internal paths, OAuth tokens, and application session material never appear in public API responses or normal logs.
10. The feature remains testable, reversible, and isolated enough that disabling it does not affect internal Asset Explorer behavior.

## 2. Non-goals for MVP

The following are explicitly out of scope unless this document is revised first:

- realtime collaborative editing;
- WebSocket presence;
- Yjs/CRDT collaboration;
- full Notion feature parity;
- arbitrary document pages unrelated to an asset;
- provider-side comments or metadata mutation;
- public upload into source providers;
- anonymous write access without a server-recognized guest session;
- search across the entire tenant corpus;
- public access to CAM administration, AI Operations, processing queues, search governance, member management, or internal dashboards;
- embedding arbitrary external URLs or HTML in annotation content;
- direct browser access to provider credentials or provider SDK tokens.

## 3. Existing CAM contracts reused by this feature

CAM already treats PostgreSQL as authoritative for authorization state and durable application identity. Source identity remains:

`tenant_id + external_source_id + external_asset_id`

Canonical content identity remains:

`tenant_id + SHA-256 content_hash`

A canonical asset may be linked to more than one source asset. Public authorization therefore must not authorize a canonical asset globally merely because one of its source links is allowed. Public review authorization must preserve the exact allowed asset/source relationship.

Existing viewer folder restrictions already establish an important invariant: selected external folders authorize their descendants server-side, and missing hierarchy data fails closed. Public Review should reuse/refactor that ancestry resolution rather than maintain a separate, divergent descendant algorithm.

## 4. Trust boundaries

The feature introduces three primary trust boundaries:

```text
Authenticated CAM browser
        |
        v
Internal share-management API
        |
        v
PostgreSQL share configuration

Public browser
        |
        v
Public Review API
        |
        v
SharePrincipal / folder-scope authorization
        |
        +------------------+
        |                  |
        v                  v
PostgreSQL assets      Provider/media adapters
and annotations        thumbnails/previews/bytes
```

Public browser input is untrusted. Share secrets are bearer-style credentials and must be treated as secrets. Guest display names and annotation documents are untrusted. Provider IDs are untrusted object references until translated and authorized. Browser coordinates for annotation pins are untrusted numeric input and must be bounded server-side.

## 5. Public-link design

### 5.1 Approved link shape

The approved public link is:

```text
https://cam.example.com/share/<public_share_id>#key=<high-entropy-secret>
```

`public_share_id` is a non-secret stable identifier. `key` is a high-entropy secret.

The secret is placed in the URL fragment rather than the path or query string. Browser fragments are not included in the HTTP request to the server, which reduces exposure through reverse-proxy request logs, web-server access logs, upstream traces, and ordinary `Referer` propagation.

The frontend reads the fragment and performs a one-time exchange over HTTPS:

```http
POST /api/public/review/<public_share_id>/session
Content-Type: application/json

{"key":"<secret>"}
```

The server:

1. hashes the supplied key with SHA-256;
2. looks up the share by tenant-independent public share ID plus stored secret digest;
3. verifies active state, revocation state, and expiry;
4. creates an opaque server-side public share session;
5. stores only the session digest;
6. sets a secure HttpOnly cookie;
7. returns safe share bootstrap metadata;
8. instructs the browser to remove the fragment from visible history/state as soon as practical.

Subsequent public API and media requests use the share-session cookie, not the raw secret.

### 5.2 Secret storage

`public_shares` stores only `secret_digest = SHA-256(secret)`. The raw secret is returned only when the share is created or explicitly rotated. It must not be recoverable later.

Normal application logs, audit logs, analytics, exceptions, database error text, and client telemetry must not contain the raw share secret or raw public share-session value.

### 5.3 Session cookie

The share-session cookie must be opaque and server-backed. Required production attributes:

- `HttpOnly`;
- `Secure`;
- `SameSite=Lax` unless a stricter setting is compatible with the route flow;
- path scoped as narrowly as practical;
- fixed expiry not exceeding the share expiry;
- server-side revocation support.

A share session becoming valid does not create a CAM application user, tenant membership, or tenant role.

## 6. Identity model

### 6.1 CurrentPrincipal remains internal

Authenticated CAM management routes continue to use `CurrentPrincipal` and tenant RBAC. The Public Review feature must not fabricate a fake CAM user or fake membership for a public reviewer.

### 6.2 SharePrincipal

Public routes resolve a dedicated `SharePrincipal` from the opaque public share-session cookie. Conceptually it contains only bounded authorization context such as:

```text
share_id
public_share_id
tenant_id
allow_comments
allow_download
expires_at
session_id/session digest reference
```

It must not contain provider credentials, OAuth tokens, internal role assignments, or unrelated tenant state.

### 6.3 Guest identity

A share session grants read access to the configured public share. Annotation writes additionally require a guest identity associated with that share session.

The first time a reviewer attempts to comment, the UI asks for a bounded display name. CAM creates `public_share_guests` state and associates it with the current share session. A guest is not a CAM user.

MVP guest identity does not require email. Email verification, company identity, SSO, and invite-only reviewer lists are future extensions.

## 7. Authorization model

### 7.1 Deny by default

Every public read or write must be authorized server-side. Frontend visibility is never an authorization boundary.

Invalid, expired, revoked, foreign, or outside-scope public objects should normally resolve to a generic not-found/unavailable response. The public API should avoid revealing whether an unauthorized folder, source, asset, annotation, or tenant exists.

### 7.2 Folder scopes

Each share contains one or more scopes:

```text
share_id + tenant_id + external_source_id + folder_external_id
```

A scope authorizes the selected folder and its descendants in the same source, subject to current provider hierarchy information.

Authorization must reuse/refactor the existing folder hierarchy resolver used by viewer folder restrictions. Missing/failed hierarchy data must fail closed. A selected root itself remains directly accessible even if local hierarchy synchronization has not yet materialized its own parent record, matching the existing safe viewer-folder behavior.

### 7.3 Asset/source identity

Authorization decisions for public assets must preserve source identity. The implementation should resolve an allowed pair such as:

```text
(asset_id, source_asset_id)
```

A canonical asset linked to both an allowed source and a disallowed source does not make the disallowed source accessible.

Provider folder/object IDs must never be accepted as internal CAM asset IDs without explicit translation through tenant/source-scoped records.

### 7.4 Public search

MVP does not expose whole-tenant search. If a later phase adds search inside a share, the result set must be intersected server-side with the exact share-authorized asset/source set. Search projection membership alone must never grant public access.

## 8. Data model contract

These tables are planned and must be introduced through a new Alembic migration on top of the current single migration head. Historical migrations must not be edited.

### 8.1 `public_shares`

Purpose: one durable public review configuration.

Required fields/concepts:

```text
id                       internal primary key
public_id                stable non-secret public identifier, unique
 tenant_id               explicit tenant owner
name                     bounded display name
secret_digest            SHA-256 only; raw secret never stored
status                   active/revoked or equivalent bounded state
allow_comments           boolean
allow_download           boolean, default false
expires_at               nullable
created_by               internal actor identifier
created_at
updated_at
revoked_at               nullable
```

Constraints must preserve tenant ownership. Secret digest must be indexed sufficiently for verification without becoming an external identifier.

### 8.2 `public_share_scopes`

Purpose: selected provider folders visible through one share.

```text
id
 tenant_id
share_id
external_source_id
folder_external_id
folder_name              optional snapshot for administration display
created_at
updated_at
```

Uniqueness:

```text
share_id + external_source_id + folder_external_id
```

Tenant/source relationships must use tenant-enforcing foreign keys or equivalent repository predicates consistent with current CAM conventions.

### 8.3 `public_share_sessions`

Purpose: server-backed access session created after successful fragment-secret exchange.

```text
id
 tenant_id
share_id
session_digest           SHA-256 of opaque cookie value
guest_id                 nullable until reviewer identifies for writing
created_at
expires_at
last_seen_at
revoked_at               nullable
```

Raw session values are never stored. Rotation or revocation of the parent share must invalidate existing sessions. A session expiry cannot exceed share expiry.

### 8.4 `public_share_guests`

Purpose: bounded reviewer identity local to a share.

```text
id
 tenant_id
share_id
display_name
created_at
updated_at
last_seen_at
```

Display name must be normalized and length-bounded. It is presentation identity only and grants no additional folder or tenant authority.

### 8.5 `asset_annotations`

Purpose: review comments and replies attached to an exact asset/source context.

```text
id
 tenant_id
share_id
asset_id
source_asset_id
guest_id
parent_annotation_id     nullable
anchor_x                 nullable normalized coordinate
anchor_y                 nullable normalized coordinate
content_json             validated rich-text document
plain_text               server-derived bounded text
status                   open/resolved or equivalent
created_at
updated_at
edited_at                 nullable
resolved_at               nullable
```

Required invariants:

- annotation share, guest, asset, and source belong to the same tenant;
- annotation asset/source pair is authorized by the share at creation time;
- reading an annotation requires current share authorization, not only historical authorization;
- replies belong to the same share and asset/source thread;
- anchor coordinates are either both null or both in `[0,1]`;
- client-supplied `plain_text` is ignored; server extracts it from validated `content_json`;
- provider metadata is never modified by annotation mutations.

## 9. Annotation content contract

### 9.1 Source of truth

Rich annotation content is stored as validated Tiptap/ProseMirror-style JSON, not generated HTML. HTML is not authoritative and must not be accepted as arbitrary trusted markup.

### 9.2 MVP node/mark allowlist

Approved feature set:

- paragraph;
- heading levels 1–3;
- bold;
- italic;
- strike;
- inline code;
- bullet list;
- ordered list;
- task list/task item;
- blockquote;
- horizontal rule;
- link;
- hard break/text nodes as required by the editor schema.

The backend must validate:

- total encoded document size;
- maximum tree depth;
- maximum node count;
- node/mark allowlist;
- bounded text length;
- link schemes (`http`/`https` only unless documentation explicitly expands them);
- structural validity.

Dangerous schemes such as `javascript:` must be rejected. The frontend must still render defensively and never inject raw annotation HTML.

### 9.3 Concurrency

MVP comments are independent documents, not a shared realtime document. Updates should use optimistic concurrency where practical, for example an `updated_at`/version precondition, to avoid silently overwriting concurrent edits.

## 10. Image annotation pins

A comment may be global to the asset or anchored to an image position.

Non-pinned comment:

```text
anchor_x = NULL
anchor_y = NULL
```

Pinned comment:

```text
0 <= anchor_x <= 1
0 <= anchor_y <= 1
```

Coordinates are calculated relative to the actual rendered image content rectangle, not the outer viewer container. Letterboxing/padding caused by `object-fit: contain` must not shift stored coordinates.

Example calculation:

```text
anchor_x = (click_x - rendered_image_left) / rendered_image_width
anchor_y = (click_y - rendered_image_top) / rendered_image_height
```

The server treats coordinates as untrusted and revalidates them. Responsive resizing and zoom must recompute display positions from normalized coordinates rather than persisting pixels.

## 11. API boundaries

### 11.1 Internal authenticated share-management API

Exact naming may follow current router conventions, but the approved functional contract is equivalent to:

```text
POST   /api/v1/public-review/shares
GET    /api/v1/public-review/shares
GET    /api/v1/public-review/shares/{share_id}
PATCH  /api/v1/public-review/shares/{share_id}
DELETE /api/v1/public-review/shares/{share_id}          # revoke, not hard delete
POST   /api/v1/public-review/shares/{share_id}/rotate-secret
```

Management routes require `CurrentPrincipal`, active tenant context, explicit tenant predicates, and the dedicated permission `public_review.manage` (or an intentionally documented replacement).

Creation/rotation may return the complete public URL once. Later reads must never reconstruct or reveal the secret.

### 11.2 Public session exchange

```text
POST /api/public/review/{public_share_id}/session
```

Input contains the fragment secret. On success it creates the opaque HttpOnly public share-session cookie and returns safe bootstrap metadata.

### 11.3 Public read API

Equivalent functional routes:

```text
GET /api/public/review/{public_share_id}/bootstrap
GET /api/public/review/{public_share_id}/folders
GET /api/public/review/{public_share_id}/folders/{folder_id}/children
GET /api/public/review/{public_share_id}/assets/{asset_id}
GET /api/public/review/{public_share_id}/assets/{asset_id}/thumbnail
GET /api/public/review/{public_share_id}/assets/{asset_id}/preview
```

All require a valid share-session cookie and repeat object authorization.

### 11.4 Guest/annotation API

Equivalent contract:

```text
POST   /api/public/review/{public_share_id}/guest
GET    /api/public/review/{public_share_id}/assets/{asset_id}/annotations
POST   /api/public/review/{public_share_id}/assets/{asset_id}/annotations
PATCH  /api/public/review/{public_share_id}/annotations/{annotation_id}
DELETE /api/public/review/{public_share_id}/annotations/{annotation_id}
POST   /api/public/review/{public_share_id}/annotations/{annotation_id}/resolve
```

Annotation writes require `allow_comments=true` and a guest associated with the current share session. A guest may edit/delete only its own annotation in MVP unless a later owner-moderation path is explicitly designed.

### 11.5 Download API

`allow_download` exists in the data model so share policy is explicit, but file download may remain unimplemented initially. No public route may treat the flag alone as permission to expose a provider URL. If download is implemented, bytes must still be delivered through an authorized CAM-controlled path.

## 12. Public media delivery

The public browser must not receive:

- Google access/refresh tokens;
- Microsoft Graph/OAuth tokens;
- provider signed credential material;
- arbitrary provider download URLs intended to act as credentials;
- local filesystem paths;
- managed-storage secrets.

Preferred delivery:

```text
public browser
  -> CAM public media endpoint
  -> SharePrincipal authorization
  -> exact asset/source authorization
  -> existing safe preview/thumbnail/provider infrastructure
  -> streamed/cached bytes
```

Authorization must be checked before media access. Media cache design must not let a revoked share continue using a stable public cache URL indefinitely.

Public API responses should use `Cache-Control: no-store` for sensitive share/session/bootstrap state. Media caching may be bounded when the cache key does not become a standalone durable authorization bypass. Revocation behavior must be tested explicitly.

## 13. Frontend architecture

Public Review is a separate frontend feature boundary under a directory such as:

```text
apps/client/app/public-review/
```

Expected components/modules include:

```text
PublicReviewApp.tsx
PublicReviewRoute.tsx
PublicReviewHeader.tsx
PublicFolderSidebar.tsx
PublicAssetGrid.tsx
PublicAssetCard.tsx
PublicAssetViewer.tsx
ReviewSidebar.tsx
AnnotationThread.tsx
AnnotationEditor.tsx
AnnotationPin.tsx
GuestIdentityDialog.tsx
api.ts
types.ts
```

The internal `App.tsx` should receive only minimal route integration.

Reusable from authenticated Asset Explorer:

- presentational primitives;
- safe media display components;
- formatters;
- breadcrumb visual patterns;
- generic layout utilities.

Not reusable without deliberate refactoring:

- authenticated API hooks;
- CurrentPrincipal assumptions;
- tenant mutation hooks;
- authenticated permission state;
- internal navigation state that could expose private surfaces.

The public page must not render internal CAM sidebar/navigation.

## 14. Public Review UX contract

### 14.1 Folder explorer

The public page presents only share roots and descendants. It may visually resemble Asset Explorer, but no hidden root/source is supplied to the client.

Required states:

- loading;
- invalid/revoked/unavailable share;
- expired share;
- empty root/folder;
- asset loading/error;
- comments disabled;
- no annotations;
- API temporarily unavailable.

### 14.2 Asset viewer

Opening an image should support:

- large contained preview;
- previous/next asset;
- left/right keyboard navigation;
- Escape to close;
- review sidebar;
- annotation count;
- responsive behavior;
- accessible controls and focus management.

### 14.3 Editor

The editor should feel Notion-like in interaction but remain intentionally smaller in scope. The approved implementation uses Tiptap with only the extensions needed for the allowed content contract.

Expected slash menu:

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

Expected inline formatting includes bold, italic, strike, inline code, and link.

No Yjs or realtime collaboration is required.

## 15. Internal share-management UX

Asset Explorer gains an action equivalent to `Share for review` on eligible folders. A share can include multiple folders across sources owned by the same tenant.

Create/edit UI must support:

- review name;
- folder scopes;
- allow comments;
- allow download policy;
- expiration;
- create link;
- copy newly created/rotated link;
- list existing links;
- rotate secret;
- revoke;
- update scopes/settings.

The UI may display the raw link only immediately after creation or rotation. Existing shares show metadata but not the secret.

Rotation and revocation require confirmation.

## 16. RBAC and audit

Internal management requires dedicated tenant permission `public_review.manage` unless a later reviewed decision introduces separate read/manage permissions.

Successful internal mutations must generate bounded secret-free audit evidence for at least:

- share created;
- share settings changed;
- scope changed;
- secret rotated;
- share revoked.

Audit records may reuse an existing suitable CAM audit facility. Do not create a parallel audit subsystem without first checking existing architecture.

Audit payloads must never contain raw share secrets, raw session cookies, provider credentials, annotation document dumps, or signed provider URLs.

## 17. CSRF, origin, and browser security

Public share sessions use cookies, therefore state-changing public endpoints must have explicit CSRF/origin defenses appropriate to the current CAM deployment. SameSite is defense in depth, not the only control.

Approved baseline:

- HTTPS only in production;
- validate `Origin` for unsafe public methods against configured CAM origins;
- reject cross-site unsafe mutations when origin is missing/invalid according to current browser/API policy;
- use JSON content type for mutation bodies;
- do not enable permissive credentialed CORS;
- set `Referrer-Policy: no-referrer` or an equally strict policy on the public review surface;
- avoid third-party analytics/scripts on the secret-exchange surface unless reviewed for secret/referrer exposure;
- apply CSP consistent with the main application and public editor requirements.

## 18. Rate limiting and abuse controls

The public surface is internet-reachable and must be rate limited.

Two layers are expected:

1. Edge/reverse-proxy rate limiting by client IP for session exchange and public endpoints.
2. Application-level limiting for sensitive operations such as secret exchange, guest creation, and annotation writes.

The implementation must inspect current topology before choosing storage for application limits. A correctness-critical limiter must not silently become per-process if multiple API replicas exist. Reuse existing durable/shared infrastructure where available; otherwise document the chosen multi-replica-safe mechanism before enabling horizontal scale.

Rate-limit responses must not reveal whether a guessed share/object exists.

## 19. Logging and observability

Allowed operational signals include bounded identifiers and counts such as:

- public share internal/public ID;
- endpoint category;
- success/failure class;
- annotation count;
- latency;
- rate-limit result;
- authorization denial reason internally when safe.

Do not log:

- raw share key;
- URL fragment;
- raw public session cookie;
- provider credential;
- signed provider URL;
- raw annotation content by default;
- full sensitive headers.

Metrics should use bounded labels; annotation text, folder names, external filenames, and unbounded object IDs should not become high-cardinality metric labels unless specifically justified.

## 20. Pagination and performance

Public folder children and annotation lists must use bounded pagination or bounded list sizes consistent with existing Explorer behavior. The frontend must not request the entire tenant corpus.

Authorization should avoid per-result provider/network calls when the existing folder ancestry cache/local source registry can resolve membership safely. Performance optimizations must never convert missing authorization data into allow behavior.

## 21. Migration contract

The schema is introduced through a new migration at the then-current Alembic head.

Requirements:

- one migration head after the change;
- non-destructive upgrade;
- downgrade removes only Public Review tables/constraints introduced by the feature;
- PostgreSQL integration test on supported versions;
- SQLite compatibility where local/test support remains a repository contract;
- no unsupported SQLite `ALTER COLUMN` operation solely to satisfy PostgreSQL behavior;
- no production migration execution by an AI agent without explicit current authorization.

Before downgrade in an environment that has real review data, disable Public Review and export/retain required review records according to operator policy.

## 22. Feature enablement and rollback

The implementation should support disabling the public surface independently from internal Asset Explorer. A feature/configuration gate is recommended if it fits current CAM conventions.

Logical rollback order:

```text
disable public routes/share creation
  -> invalidate/revoke active public sessions
  -> deploy previous application version
  -> retain new tables while investigating
  -> only downgrade schema after confirming no required review data will be lost
```

Application rollback should not require immediate schema downgrade when additive tables remain harmless.

## 23. Required security regression scenarios

These behaviors are mandatory test targets:

```text
valid share -> selected root                         => allowed
valid share -> descendant folder                    => allowed
valid share -> sibling folder                       => denied/not found
valid share -> unselected root                      => denied/not found
share_A -> tenant_B asset                           => denied/not found
provider object ID used as internal asset ID        => denied
allowed canonical asset through disallowed source   => denied
missing hierarchy data                              => fail closed
invalid public key                                  => denied
expired share                                       => denied
revoked share                                       => denied
rotated old key                                     => denied
revoked preexisting public session                  => denied
thumbnail outside scope                             => denied
preview outside scope                               => denied
annotation outside scope                            => denied
allow_comments=false write                          => denied
guest_A edits guest_B annotation                    => denied
cross-share annotation ID                           => denied
cross-asset reply                                   => denied
invalid anchor                                      => rejected
oversized rich-text document                        => rejected
excessive rich-text depth/node count                => rejected
dangerous link scheme                               => rejected
public principal -> internal management API         => denied
internal principal lacking public_review.manage     => denied
raw share/session secret in persistence/log/audit   => absent
annotation mutation -> provider metadata            => unchanged
```

## 24. Phase ownership

Implementation is intentionally split into phases. The canonical sequencing and acceptance gates are defined in:

`docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`

Codex/AI execution rules and completion-report requirements are defined in:

`docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`

An implementation phase must not opportunistically pull work from later phases merely because it is convenient. Small focused diffs are part of the security model.

## 25. Future extensions

Possible later additions, each requiring an explicit design update:

- invite-only reviewer email verification;
- password-protected shares;
- reviewer role variants;
- mentions/notifications;
- owner moderation UI;
- annotation activity feed;
- video timeline annotations;
- rectangle/freehand region annotations;
- public search constrained to share scope;
- analytics such as unique reviewers and review completion;
- realtime collaboration;
- external client branding.

None of these are implicit MVP requirements.

## 26. Decision summary

The approved design is:

```text
Authenticated CAM
  -> create/manage share
  -> explicit provider-folder scopes
  -> PostgreSQL durable configuration

Public link
  /share/<public_id>#key=<secret>
  -> fragment secret exchange
  -> opaque HttpOnly public share session
  -> SharePrincipal
  -> shared/refactored folder ancestry authorization
  -> exact asset/source authorization
  -> CAM-controlled preview/thumbnail delivery
  -> CAM-owned rich-text annotations
```

The Public Review Portal is a separate security boundary, not a less-protected Asset Explorer. Its UI may reuse presentation components; its authorization, public session handling, public APIs, and mutation rules must remain explicit and server-enforced.
