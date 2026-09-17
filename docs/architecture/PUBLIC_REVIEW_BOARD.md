# Public Review Board Architecture

**Status:** APPROVED DESIGN — NOT YET IMPLEMENTED  
**Parent architecture:** `docs/architecture/PUBLIC_REVIEW.md`  
**Implementation plan:** `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`  
**Codex execution rules:** `docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`  
**Primary branch:** `main`  
**Last updated:** 2026-09-17

This document is the canonical architecture contract for the authenticated **Review Board** that accompanies the CAM Public Review Portal. It extends the Public Review architecture and is part of the MVP. If this document conflicts with an older statement in `PUBLIC_REVIEW.md` that classified owner moderation, annotation activity, review statistics, or completion tracking as a future extension, **this document supersedes that older statement**.

The Review Board is an internal CAM workspace, visible from the primary application sidebar beside Asset Explorer, AI Operations, Job Queue, Video Generation, and Access Management. It provides one place for internal users to see review notes submitted from public shares, understand review workload, open the exact affected asset, and mark an issue resolved when the requested change has been completed.

## 1. Product goal

Public Review solves external feedback collection. Review Board solves internal feedback operations.

The intended flow is:

```text
CAM user creates a public review share
        |
        v
External reviewer browses allowed folders/assets
        |
        v
External reviewer adds comments / pinned annotations
        |
        v
PostgreSQL asset_annotations
        |
        v
Authenticated Review Board
        |
        +--> triage open feedback
        +--> inspect affected asset and thread
        +--> mark Done / Resolved
        +--> reopen when necessary
        +--> monitor workload and completion statistics
```

Review Board does not create a parallel task system. `asset_annotations` remains the authoritative review-issue record. Resolution state is represented by the annotation's durable status and resolution metadata.

## 2. Sidebar and route contract

The authenticated application sidebar must add a first-class navigation item:

```text
Creative assets

Asset Explorer
AI Operations
Job Queue
Video Generation
Review Board
Access Management
```

Exact ordering may be adjusted to current product navigation conventions, but Review Board must be a distinct authenticated route rather than hidden inside the public portal.

Recommended route:

```text
/review-board
```

Recommended feature boundary:

```text
apps/client/app/review-board/
```

Expected high-level modules:

```text
ReviewBoardPage.tsx
ReviewBoardStats.tsx
ReviewBoardFilters.tsx
ReviewAnnotationList.tsx
ReviewAnnotationRow.tsx
ReviewAnnotationDetail.tsx
ReviewAssetPreview.tsx
ReviewStatusControl.tsx
api.ts
types.ts
*.test.tsx
```

The public-review frontend remains under `apps/client/app/public-review/`. Do not merge authenticated Review Board data hooks into the unauthenticated public-review bundle merely for code convenience.

## 3. Review item definition

For Review Board purposes, one top-level annotation is one review issue.

A top-level annotation is:

```text
parent_annotation_id IS NULL
```

Replies are conversation entries belonging to that issue. Replies do not independently increase the open-issue count unless a later reviewed design explicitly introduces sub-issue semantics.

Pinned and non-pinned top-level annotations are treated equally for issue workflow:

```text
pinned annotation    -> one issue with anchor_x/anchor_y
non-pinned annotation -> one issue with no anchor
```

The board must preserve exact asset/source identity and must not infer asset identity from filename, folder name, URL, or provider path.

## 4. Resolution lifecycle

The canonical MVP statuses are:

```text
open
resolved
```

UI language may use **Done** as the primary action label and **Resolved** as the durable status label.

Allowed transitions:

```text
open -> resolved
resolved -> open
```

Resolving must persist at least:

```text
status = resolved
resolved_at
resolved_by
```

Reopening must persist at least:

```text
status = open
resolved_at = null
resolved_by = null
```

`resolved_by` is the authenticated internal CAM actor identifier. A public guest does not mark issues Done in the MVP.

A resolution mutation must be auditable and tenant-scoped.

The board must never mark an issue resolved merely because a reply was posted, an asset changed, a file was re-uploaded, or a provider modified timestamp changed. Resolution is an explicit internal human action.

## 5. Annotation data-model extension

`asset_annotations` defined by the parent Public Review architecture remains authoritative. The implementation must include or extend these workflow fields:

```text
status                  open/resolved
resolved_at             nullable
resolved_by             nullable authenticated CAM actor
```

Recommended optional field when current audit conventions benefit from it:

```text
resolution_note         nullable bounded internal note
```

Do not add `resolution_note` merely because this document mentions it; Phase 9 should inspect actual UX and audit conventions first. The MVP requires `status`, `resolved_at`, and `resolved_by`.

The normal annotation body remains the guest-authored rich-text document. Resolution metadata is separate and must not rewrite guest content.

## 6. Internal RBAC

Review Board introduces a clearer permission split than the original single-management-permission draft.

Approved tenant permissions:

```text
public_review.read
public_review.resolve
public_review.manage
```

Semantics:

### `public_review.read`

Allows an authenticated principal to:

- open Review Board;
- list review issues for the active tenant;
- view safe review statistics;
- inspect annotation threads and allowed asset preview information;
- use filters and search limited to review records the principal may read.

It does not allow share mutation or resolution mutation.

### `public_review.resolve`

Allows an authenticated principal to:

- perform everything allowed by `public_review.read` as defined by role composition or explicit grants;
- mark an open issue resolved/Done;
- reopen a resolved issue.

It does not by itself allow creating, rotating, revoking, or changing public shares.

### `public_review.manage`

Allows share-management operations:

- create public review share;
- update scopes/settings;
- rotate share secret;
- revoke share;
- manage share expiration and download/comment policy.

A role that manages shares should normally also receive `public_review.read`, but code must check the exact required permission for each endpoint rather than infer unrelated authority from UI visibility.

Platform administration remains separate from tenant permissions.

## 7. Board list contract

The primary Review Board view is issue-centric, grouped or filterable by asset.

Each row/card should expose bounded useful context:

```text
[Done checkbox] [asset thumbnail]
asset filename/display name
folder/share context
reviewer display name
annotation preview
pin indicator when anchored
reply count
created_at
status
resolved_at / resolver when resolved
```

Selecting a row opens detail without losing board filters.

The detail view should include:

- larger asset preview;
- pin overlay when the annotation is anchored;
- full rich-text annotation;
- replies/thread;
- share name;
- relevant source/folder context;
- reviewer display name;
- timestamps;
- status history/audit information where safely available;
- Done/Resolve or Reopen action according to current state and permission.

The board should offer a direct action to open the corresponding asset in authenticated Asset Explorer when the current principal is authorized to view that asset there. Do not construct a link that bypasses Asset Explorer authorization.

## 8. Filters and sorting

MVP filters:

```text
Status:       Open | Resolved | All
Share:        one or more review shares
Folder:       scoped folder context when available
Asset:        bounded filename/display-name query or asset selector
Reviewer:     guest display name
Date range:   created_at
Pinned:       pinned | unpinned | all
```

Recommended default:

```text
Status = Open
Sort = newest unresolved first
```

Additional useful sort options:

```text
Newest
Oldest
Recently updated
Recently resolved
```

Filters must be implemented server-side or with bounded server pagination. Do not load all tenant annotations into the browser and then filter them locally.

## 9. Statistics contract

The board includes a statistics area computed from PostgreSQL-authoritative review state.

MVP summary metrics:

```text
Total issues
Open issues
Resolved issues
Resolution rate
Assets with open issues
Shares with open issues
```

Derived metric:

```text
resolution_rate = resolved_issues / total_issues
```

When total is zero, represent the rate safely as zero/no-data according to UI conventions rather than divide by zero.

Optional useful metrics if cheap and unambiguous:

```text
New issues in selected period
Resolved in selected period
Average resolution time
Median resolution time
Open issues by share
Open issues by folder
Open issues by asset
```

Do not introduce a separate analytics database for MVP. These statistics are operational aggregates over authoritative PostgreSQL review records. If volume later requires projections/materialization, that is a separately reviewed optimization and must remain rebuildable from PostgreSQL.

Statistics must respect the active tenant and the current user's permissions. They must not count foreign-tenant records.

## 10. Counting rules

To keep metrics stable and comprehensible:

- counts operate on top-level annotations/issues only;
- replies do not increment issue totals;
- deleted annotations are excluded according to the final deletion/retention model;
- resolved issues remain in total and resolved counts;
- open count includes only `status=open`;
- a single asset with multiple open top-level annotations counts once in `assets_with_open_issues`;
- a single share with multiple open annotations counts once in `shares_with_open_issues`;
- statistics must define the time zone/time-window behavior explicitly in API tests if date filtering is added.

## 11. Internal API contract

Exact route naming may follow repository conventions, but the approved functional contract is equivalent to:

```text
GET  /api/v1/public-review/board/issues
GET  /api/v1/public-review/board/issues/{annotation_id}
GET  /api/v1/public-review/board/stats
POST /api/v1/public-review/board/issues/{annotation_id}/resolve
POST /api/v1/public-review/board/issues/{annotation_id}/reopen
```

`GET .../issues` supports bounded pagination and filters.

Suggested query concepts:

```text
status
share_id
external_source_id
folder_external_id
asset_id
reviewer
pinned
created_from
created_to
sort
page/page_size or cursor
```

Do not accept a tenant ID from the browser as authority. Tenant scope comes from `CurrentPrincipal.active_tenant_id`; any explicit tenant path/query parameter must be independently validated if repository conventions require one.

## 12. Repository/query requirements

Every board query must include explicit tenant predicates.

Issue hydration may join:

```text
asset_annotations
public_shares
public_share_guests
assets
source_assets / asset_source_links
```

but joins must preserve tenant-qualified relationships and exact source identity.

Do not use annotation `share_id` alone as proof that the current principal may access an unrelated asset.

Board queries should avoid N+1 per-row provider requests. Prefer PostgreSQL/source registry metadata and current managed thumbnail/preview infrastructure.

## 13. Resolution mutation requirements

Resolve/Reopen is security-sensitive mutation behavior.

Resolve request must verify:

1. authenticated `CurrentPrincipal`;
2. active tenant membership;
3. `public_review.resolve`;
4. annotation belongs to active tenant;
5. target is a top-level annotation issue unless explicitly supporting thread-level status later;
6. current state permits the transition;
7. mutation is transactionally persisted;
8. secret-free audit evidence is recorded.

Repeated resolve of an already resolved issue and repeated reopen of an open issue should be idempotent or return a stable no-op result rather than creating contradictory state.

## 14. Audit contract

At minimum record bounded audit events for:

```text
review_issue_resolved
review_issue_reopened
```

Audit metadata may contain:

```text
tenant_id
annotation_id
share_id
asset_id
actor user_id
old_status
new_status
timestamp
bounded reason if later supported
```

Audit metadata must not contain:

- raw public share secret;
- public share-session cookie;
- provider credentials;
- full annotation rich-text document;
- signed provider URLs;
- arbitrary file bytes.

## 15. UI statistics layout

A recommended desktop layout is:

```text
Review Board

[ Total 128 ] [ Open 34 ] [ Resolved 94 ] [ Resolution 73% ]

Status: Open   Share: All   Folder: All   Reviewer: All   Search...

[ ]  IMG_001.jpg   Logo should be 10% smaller        Alex     2 replies
[✓]  IMG_002.jpg   Color looks too warm              Sarah    Done
[ ]  IMG_003.jpg   Please remove background object   John     1 reply
```

Clicking an issue opens a right-side detail panel or dedicated detail view:

```text
┌─────────────────────────────┬──────────────────────────────┐
│                             │ IMG_001.jpg                  │
│        asset preview        │ Alex                         │
│             ①               │ Logo should be 10% smaller  │
│                             │                              │
│                             │ Replies...                   │
│                             │                              │
│                             │ [ Mark Done ]                │
└─────────────────────────────┴──────────────────────────────┘
```

The Done checkbox/action must be permission-aware. Hiding the control is not authorization; the API remains authoritative.

## 16. Empty/error/loading states

The board must explicitly support:

- loading list;
- loading stats;
- no review issues yet;
- no open issues;
- filters produce no results;
- issue was deleted/unavailable;
- asset preview unavailable while annotation remains readable;
- unauthorized board route;
- resolve mutation pending;
- resolve mutation failed;
- concurrent state changed elsewhere;
- API unavailable.

Optimistic UI may be used for Done/Reopen only if failures roll back visually and the server result remains authoritative.

## 17. Public-side relationship

A public reviewer can create/reply/edit own allowed annotations according to the parent architecture, but cannot use the internal Review Board APIs.

When an internal user marks an issue resolved:

- the public thread should display resolved state if the public UI exposes status;
- existing guest content remains unchanged;
- the public guest cannot silently reopen it in MVP;
- a new independent top-level annotation may still be created if comments remain enabled.

A future design may allow external reviewer acknowledgement/reopen requests, but that is not part of MVP.

## 18. Performance and pagination

Issue list must be paginated.

Indexes should support dominant filters after actual query plans are inspected, likely including combinations around:

```text
tenant_id + status + created_at
tenant_id + share_id + status
tenant_id + asset_id + status
```

Do not add speculative indexes blindly in the architecture phase. Phase 9 should inspect PostgreSQL query patterns and add only justified indexes through a migration if the Phase 1 annotation table indexes are insufficient.

Stats queries must be bounded by tenant and optional filters. Avoid issuing one query per statistic when one/few aggregate queries can safely return the dashboard summary.

## 19. Security regression requirements

Mandatory Review Board tests include:

```text
user without public_review.read -> board list/stats denied
user with read but without resolve -> resolve/reopen denied
user with resolve -> own-tenant issue resolve allowed
principal_A -> tenant_B annotation -> denied/not found
foreign annotation ID enumeration -> no data leak
stats -> foreign tenant records excluded
reply rows -> excluded from issue total
multiple open issues on one asset -> assets_with_open_issues counts one
resolved issue -> moves from open to resolved counts
resolve -> resolved_by authenticated actor persisted
reopen -> resolution metadata cleared consistently
repeated resolve/reopen -> stable/idempotent behavior
public SharePrincipal -> internal board APIs denied
resolve/reopen -> provider metadata unchanged
resolve/reopen -> guest rich-text content unchanged
audit -> raw secrets/content dumps absent
```

## 20. MVP boundary

The authenticated Review Board is now part of the Public Review MVP.

MVP includes:

- sidebar Review Board tab;
- issue list grouped/filterable by asset context;
- top-level annotation issue semantics;
- Open/Resolved state;
- Done/Resolve and Reopen;
- summary statistics;
- pagination and filters;
- thread/detail view;
- audit trail for resolution changes;
- direct navigation to authorized Asset Explorer asset where practical.

Still future work:

- assigning issues to internal users;
- due dates;
- priority/severity;
- kanban workflow beyond Open/Resolved;
- external reviewer reopen requests;
- notifications/mentions;
- realtime updates/WebSocket presence;
- SLA dashboards;
- automatic resolution inferred from file changes;
- AI-generated triage or automatic issue classification.

## 21. Implementation phase

Review Board implementation is **Phase 9** of the Public Review implementation plan.

Phase 9 begins only after the public sharing, annotation, internal share-management, and rich-editor foundations from earlier phases are complete and passing their required tests.

Codex must read this document before Phase 9 and must not implement future workflow features unless this architecture is explicitly revised first.
