# Public Review Bearer-Link Access and Scoped Search

**Status:** APPROVED ARCHITECTURE AMENDMENT  
**Parent architecture:** `docs/architecture/PUBLIC_REVIEW.md`  
**Applies to:** public link access, anonymous guest identity, share-scoped search, public review writes  
**Primary branch:** `main`  
**Last updated:** 2026-09-17

This document records the approved product contract that **any person who possesses a valid Public Review link may use the review workspace without a CAM account**. It supersedes older Public Review statements that made reviewer display-name entry mandatory before writing or treated search inside a share as future-only work.

The existing share-secret/session, tenant isolation, folder-scope, exact asset/source identity, media authorization, CSRF/origin, rate limiting, and revocation contracts remain in force.

## 1. Product contract

A valid Public Review URL is a bearer capability:

```text
/share/<public_share_id>#key=<high-entropy-secret>
```

Any person who has that valid, active, non-expired, non-revoked link may, subject to the share configuration:

- open the Public Review workspace;
- browse the explicitly shared folder roots and their descendants;
- view authorized asset metadata, thumbnails, and previews;
- search assets inside the share-authorized scope;
- read review notes/threads on authorized assets;
- create notes and replies when `allow_comments=true`;
- add image pins when the pin phase is implemented;
- edit/delete annotations owned by the same anonymous review identity/session according to the annotation ownership rules.

The reviewer does **not** need:

- a CAM account;
- tenant membership;
- email verification;
- SSO;
- a password;
- a mandatory display-name prompt before interacting.

Possession of the link is the access credential. Therefore users must be informed that forwarding the link forwards its access capability.

## 2. What the link does not grant

A valid link never grants access to:

- folders or assets outside the share scopes;
- another source merely because the canonical asset is the same;
- whole-tenant search;
- authenticated CAM routes;
- Asset Explorer private state;
- AI Operations, Job Queue, Video Generation, Access Management, Review Board, or tenant administration;
- provider credentials, signed credential URLs, local paths, OAuth tokens, or internal application sessions;
- internal Review Board `Done/Resolve/Reopen` authority.

Public review authorization remains deny-by-default and server-enforced on every read, search result, media request, and annotation mutation.

## 3. Session model remains mandatory

"Anyone with the link" does not mean stateless unauthenticated endpoints.

The existing fragment-secret exchange remains mandatory:

```text
link secret
  -> POST public session exchange
  -> opaque HttpOnly server-backed share session
  -> SharePrincipal
  -> scoped public operations
```

The raw link secret is never persisted and must be removed from visible browser history/state as soon as practical after exchange.

Revocation, rotation, or expiry invalidates access according to the parent Public Review architecture.

## 4. Anonymous guest identity

### 4.1 No mandatory name dialog

Annotation writes still need a server-recognized guest identity for ownership and abuse controls, but the reviewer must not be forced to type a name before commenting.

When a valid share session performs its first annotation mutation and has no guest attached, CAM automatically creates a `public_share_guests` row and associates it with that share session.

Because Phase 1 stores a non-null bounded `display_name`, the automatically created identity uses a server-generated presentation name such as:

```text
Guest 7F3A
```

The suffix must be non-sensitive and need not expose any internal primary key or session token.

A later optional UI action may let the reviewer change that display name. Naming is presentation only and never increases authority.

### 4.2 Ownership semantics

The anonymous guest is scoped to the current share and share session.

The same session may edit/delete its own annotations. Another browser/session opening the same bearer link is a separate anonymous guest unless future reviewed identity linking is added.

Public guests do not gain internal resolve/reopen rights. Internal Review Board resolution remains an authenticated CAM operation.

## 5. Share-scoped public search

Search is part of the Public Review MVP.

Recommended functional endpoint:

```text
GET /api/public/review/{public_share_id}/search?q=<query>&cursor=<cursor>
```

Exact route naming may follow repository conventions.

### 5.1 Authorization invariant

Every returned search result must be an exact share-authorized asset/source pair.

Conceptually:

```text
search candidates
  INTERSECT
share-authorized (asset_id, source_asset_id) pairs
  -> public results
```

Search-index membership never grants access by itself.

A canonical asset visible through one allowed source must not expose a disallowed source for that same canonical asset.

### 5.2 Search corpus

Public search covers only:

- selected share roots;
- their currently authorized descendants;
- authorized assets/source links inside those folders.

It must not search or expose the rest of the tenant corpus.

### 5.3 Search implementation

Reuse the existing CAM search parser/projection where practical, but place share authorization inside the server-side result path.

Preferred implementation order:

1. constrain search by tenant;
2. constrain by source/share scope as early as current search infrastructure safely permits;
3. resolve candidate internal asset/source identities;
4. apply the Phase 2 share authorization service to every result or bounded candidate set;
5. return only authorized public DTOs.

Do not rely on client-side filtering.

Do not fetch an unbounded whole-tenant result set and filter it in the browser.

If the search backend cannot safely express exact source/folder scope at query time, use bounded candidate retrieval plus server-side authorization filtering with pagination designed so unauthorized candidates never appear in counts or response data.

### 5.4 Search response minimization

Public search must not leak outside-scope information through:

- result counts;
- facets;
- autocomplete/suggestions;
- highlighted text;
- filenames;
- paths;
- folder names;
- source names;
- error messages;
- timing-dependent existence responses where reasonably avoidable.

Whole-tenant suggestions/autocomplete are disabled on the public surface unless they are independently constrained to the share-authorized corpus.

## 6. Annotation/write behavior

When `allow_comments=true`, a valid bearer-link session may create notes/replies without additional authentication.

Write flow:

```text
valid SharePrincipal
  -> verify share active + allow_comments
  -> ensure/create anonymous guest for current share session
  -> verify exact asset/source is currently share-authorized
  -> validate content / anchor / Origin-CSRF policy / rate limit
  -> persist annotation
```

When `allow_comments=false`, the share remains browse/search/read-only and annotation mutations are denied.

The default share configuration may keep `allow_comments=true`, matching the Phase 1 model default.

## 7. Frontend UX

The Public Review page must not present CAM login as a prerequisite.

Required MVP interaction:

```text
open link
 -> exchange secret
 -> browse immediately
 -> search immediately
 -> open asset
 -> read notes
 -> write note immediately when comments are enabled
```

There is no mandatory `GuestIdentityDialog` before the first note. If a guest-name control remains in the component architecture, it is optional profile/display-name editing rather than an authorization gate.

Public search should be visually available in the Public Review workspace and operate only on the share-scoped API.

## 8. Phase deltas

This amendment changes the implementation sequence as follows.

### Phase 3 — Share management

No new public endpoint is added. Share creation continues to support `allow_comments`; the default remains compatible with interaction-enabled links. Management UI may later communicate that anyone with the link can access the share.

### Phase 4 — Public session + read-only Explorer API

Phase 4 must now include the **share-scoped public search backend** in addition to session exchange, folders, assets, thumbnails, and previews.

Required search tests include:

- selected-root asset returned;
- descendant asset returned;
- sibling/unselected root excluded;
- tenant-B asset excluded;
- allowed canonical asset through wrong/disallowed source excluded;
- provider ID cannot bypass internal identity translation;
- unauthorized candidates do not leak through counts/facets/suggestions;
- revoked/expired share cannot search.

### Phase 5 — Guest + annotation backend

Phase 5 must automatically provision a server-recognized anonymous guest on first write when the share session has no guest.

A display-name request is optional, not required for writing.

Required tests include:

- valid share session can comment without entering a name;
- first write creates/attaches one anonymous guest;
- repeated writes in the same session reuse that guest;
- separate sessions receive separate guest identities;
- one guest cannot edit/delete another guest's annotation;
- `allow_comments=false` blocks anonymous writes.

### Phase 6 — Public Review frontend MVP

Phase 6 must include a search UI using only the public share-scoped search API.

Remove any requirement that the reviewer complete a guest-name dialog before creating the first note. Optional rename/profile UX may remain.

### Phase 7 — Rich text and pins

No access-model change. The same bearer-link/share-session/anonymous-guest rules apply to rich comments and pins.

### Phase 9 — Review Board

No change. Resolution/Done/Reopen remains authenticated internal CAM functionality.

## 9. Security regression requirements

In addition to the parent Public Review regression matrix, test:

```text
valid link/session -> scoped search                       allowed
valid link/session -> note without typed display name    allowed when allow_comments=true
valid link/session -> tenant-wide search                  impossible
authorized asset -> unauthorized source search result    excluded
sibling/unselected folder search result                  excluded
revoked/expired share -> search                           denied
allow_comments=false -> anonymous write                  denied
session A guest -> edit session B annotation             denied
public guest -> internal resolve/reopen                  denied
```

## 10. Decision summary

The approved UX is intentionally friction-light:

```text
Anyone with valid link
  -> no CAM login
  -> no mandatory name/email
  -> browse shared folders
  -> view shared assets
  -> search only inside share scope
  -> read notes
  -> create notes/replies/pins when comments enabled
```

The security boundary is not reviewer identity; it is the high-entropy bearer link plus the server-backed share session and exact server-side share scope authorization.
