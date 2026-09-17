# Public Review Phase 9 — Internal Review Board

**Status:** APPROVED IMPLEMENTATION PHASE  
**Parent plan:** `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`  
**Architecture:** `docs/architecture/PUBLIC_REVIEW.md` and `docs/architecture/PUBLIC_REVIEW_BOARD.md`  
**Execution rules:** `docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`  
**Last updated:** 2026-09-17

This document extends the existing Public Review implementation plan with **Phase 9**. It is mandatory for the Public Review MVP.

If Phase 1 has not yet been implemented, fields and permission catalog entries required here should be incorporated into the earliest appropriate phase to avoid unnecessary follow-up migrations. If earlier phases are already complete, Phase 9 must introduce only additive, backward-compatible changes and must not rewrite historical migrations.

## 1. Mandatory preflight

Before editing:

1. Read `AGENTS.md`.
2. Read `docs/security/SECURITY_GOVERNANCE.md`.
3. Read `docs/architecture/AGENT.md`.
4. Read `docs/architecture/SECURITY.md`.
5. Read `docs/architecture/DATA_MODEL.md`.
6. Read `docs/architecture/PUBLIC_REVIEW.md`.
7. Read `docs/architecture/PUBLIC_REVIEW_BOARD.md` in full.
8. Read `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`.
9. Read `docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md`.
10. Inspect the current annotation models, repositories, RBAC catalog, share-management APIs, frontend sidebar/router, Asset Explorer navigation, and existing audit infrastructure.
11. Record current branch SHA and baseline failures before changing code.

Do not begin from chat memory. Repository docs are authoritative.

## 2. Objective

Add a first-class authenticated **Review Board** route to CAM so internal users can:

- see review notes/issues grouped and filterable by asset context;
- see review workload statistics;
- inspect the exact affected asset and annotation thread;
- mark an open issue Done/Resolved;
- reopen a resolved issue;
- navigate to the corresponding authenticated Asset Explorer asset when permitted.

Do not build a separate task/workflow database. `asset_annotations` remains authoritative.

## 3. Scope

Phase 9 includes:

### Backend

- RBAC permissions `public_review.read`, `public_review.resolve`, `public_review.manage` as documented;
- internal board list endpoint;
- internal board detail endpoint;
- internal board statistics endpoint;
- resolve mutation;
- reopen mutation;
- explicit tenant predicates;
- top-level annotation issue semantics;
- audit events for resolve/reopen;
- pagination and server-side filters;
- justified indexes if current schema/query plans require them.

### Frontend

- sidebar Review Board item;
- authenticated `/review-board` route or repository-conventional equivalent;
- summary statistics cards;
- issue list/table/cards;
- default Open filter;
- share/folder/asset/reviewer/date/pinned filters as practical for MVP;
- detail panel/view;
- asset preview and annotation pin context;
- Done/Resolve action;
- Reopen action;
- direct authorized Asset Explorer navigation;
- loading/empty/error/permission states.

### Documentation

Update current-state/data-model/API/review/roadmap docs where repository rules require it.

## 4. Out of scope

Do not add in Phase 9:

- assignee;
- priority;
- due date;
- kanban columns beyond Open/Resolved;
- notifications;
- @mentions;
- realtime WebSockets;
- AI triage;
- automatic resolution based on file changes;
- external reviewer reopen permission;
- SLA reporting;
- new analytics database.

## 5. Data model handling

Required durable resolution metadata:

```text
asset_annotations.status       open/resolved
asset_annotations.resolved_at  nullable
asset_annotations.resolved_by  nullable authenticated CAM actor
```

If `resolved_by` does not exist:

- when Phase 1 is still unimplemented: include it in the initial Public Review migration/model;
- when Phase 1 is already merged/deployed: add a new additive Alembic migration on the then-current single head.

Do not edit a historical migration that has already been merged/deployed.

If a foreign key for `resolved_by` would create undesirable coupling to existing user-retention semantics, follow the existing audit actor convention and document the chosen durable actor representation. Tenant isolation must remain explicit.

## 6. Permission migration strategy

The approved permission keys are:

```text
public_review.read
public_review.resolve
public_review.manage
```

Inspect existing permission catalog/seed behavior before implementing.

Required endpoint permissions:

```text
board list/detail/stats       -> public_review.read
resolve/reopen                -> public_review.resolve
share create/update/revoke    -> public_review.manage
```

Do not use sidebar visibility as authorization.

Roles may contain multiple permissions. Do not automatically treat every reader as resolver or every resolver as share manager.

If earlier phases already introduced only `public_review.manage`, migrate carefully to the three-key model without silently removing effective access from intended existing admin/system roles. Add tests for role/catalog behavior.

## 7. API contract

Equivalent functional routes:

```text
GET  /api/v1/public-review/board/issues
GET  /api/v1/public-review/board/issues/{annotation_id}
GET  /api/v1/public-review/board/stats
POST /api/v1/public-review/board/issues/{annotation_id}/resolve
POST /api/v1/public-review/board/issues/{annotation_id}/reopen
```

Follow actual router conventions if naming differs, but preserve the functional/security contract.

### List filters

Support bounded variants of:

```text
status=open|resolved|all
share_id
external_source_id
folder_external_id
asset_id
reviewer
pinned
created_from
created_to
sort
pagination
```

Default status should be `open`.

Default sorting should prioritize actionable unresolved feedback, normally newest open first unless current UX conventions strongly favor another deterministic ordering.

### Stats response

At minimum return safe numeric values for:

```text
total_issues
open_issues
resolved_issues
resolution_rate
assets_with_open_issues
shares_with_open_issues
```

Optional additions may include period counts and resolution-time metrics if implemented efficiently and unambiguously.

Stats operate on top-level annotations only.

## 8. Issue semantics

One top-level annotation equals one issue:

```text
parent_annotation_id IS NULL
```

Replies remain part of the thread and must not inflate issue totals.

Pinned and non-pinned top-level annotations are both issues.

Deleted/retained states must follow the final annotation retention semantics and be tested.

## 9. Resolve transition

Resolve operation:

```text
open
 -> resolved
status       = resolved
resolved_at  = server current timestamp
resolved_by  = CurrentPrincipal user/actor ID according to chosen convention
```

The guest-authored rich-text body is unchanged.

Resolve is explicit human workflow. Do not infer it from asset/provider changes.

Resolve should be idempotent or produce a stable no-op response when already resolved.

## 10. Reopen transition

Reopen operation:

```text
resolved
 -> open
status       = open
resolved_at  = null
resolved_by  = null
```

Reopen should be idempotent/stable when already open.

Record audit evidence.

## 11. Repository and query implementation

Prefer a dedicated board query/service layer within the Public Review backend module rather than placing aggregation SQL in routers.

Every query retains explicit active-tenant predicates.

Avoid per-row provider API calls. Hydrate from PostgreSQL/source registry and safe existing preview infrastructure.

Inspect query plans before adding indexes. Candidate access patterns include:

```text
(tenant_id, status, created_at)
(tenant_id, share_id, status)
(tenant_id, asset_id, status)
```

Only add indexes justified by actual board queries.

## 12. Frontend architecture

Create a dedicated authenticated feature boundary, for example:

```text
apps/client/app/review-board/
```

Recommended modules:

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
```

Add Review Board to the authenticated sidebar shown with the existing application modules.

Do not bundle it into the public `/share/...` route.

## 13. UX acceptance contract

Initial page:

```text
Review Board

[ Total ] [ Open ] [ Resolved ] [ Resolution % ]

Status: Open | Share | Folder | Reviewer | Search

[ ] image A   reviewer note...            Alex   2 replies
[ ] image B   reviewer note...            John   pinned
[✓] image C   reviewer note...            Sarah  resolved
```

Selecting an issue exposes:

- asset preview;
- pin position when relevant;
- full annotation;
- replies;
- reviewer;
- share/folder context;
- timestamps;
- current status;
- resolver/resolution timestamp when resolved;
- Mark Done or Reopen depending on state/permission.

Checkbox semantics:

```text
unchecked -> open
checked   -> resolved
```

The UI may use checkbox interaction, but the underlying transition still uses explicit server resolve/reopen operations and audit.

Optimistic updates are allowed only with rollback on server failure.

## 14. Required negative/security tests

Backend tests must include at least:

```text
no public_review.read -> list denied
no public_review.read -> stats denied
read without resolve -> resolve denied
read without resolve -> reopen denied
resolve permission -> own-tenant resolve allowed
principal_A -> tenant_B annotation denied/not found
foreign annotation enumeration leaks no data
public SharePrincipal -> internal board APIs denied
reply rows excluded from issue count
foreign tenant rows excluded from stats
same asset multiple open issues -> assets_with_open_issues distinct count
same share multiple open issues -> shares_with_open_issues distinct count
resolve persists authenticated resolver
resolve changes open/resolved statistics
reopen reverses state/statistics
repeat resolve stable/idempotent
repeat reopen stable/idempotent
resolve/reopen leaves provider metadata unchanged
resolve/reopen leaves guest content unchanged
audit contains no raw share/session secret or annotation dump
```

Frontend tests must include at least:

```text
sidebar visibility follows permission summary
route unauthorized state
stats render
open default filter
resolved filter
issue row grouped with correct asset context
Done control hidden/disabled without resolve permission
Done triggers resolve and updates UI
failed resolve rolls back optimistic state if used
Reopen works
thread replies do not appear as separate issue rows
asset navigation uses authenticated route and cannot bypass authorization
```

## 15. Statistics correctness tests

Add focused tests for:

```text
zero total -> safe resolution_rate
10 total / 7 resolved -> 70 percent representation per API contract
replies do not increase total
resolved issues remain in total
open counts only status=open
distinct asset count is distinct asset, not number of annotations
distinct share count is distinct share, not number of annotations
date filters use documented boundary/time-zone behavior
```

Avoid floating-point ambiguity in persistence. The API can return integer counts plus a derived percentage/ratio according to schema conventions.

## 16. Audit requirements

Audit events:

```text
review_issue_resolved
review_issue_reopened
```

Must identify actor and bounded object IDs without raw guest content or secrets.

## 17. Test commands

Codex must inspect current repository scripts and use the actual supported commands rather than blindly assuming names.

Expected coverage includes:

### Backend

- Public Review unit tests;
- authorization/RBAC tests;
- repository tests;
- migration tests if schema/catalog changes;
- supported PostgreSQL integration migration tests when a new migration is added.

### Frontend

```text
npm test
npm run typecheck
npm run build
```

plus focused Review Board tests.

Run broader shared tests when shared authorization/explorer/navigation code is modified.

## 18. Independent review pass

After implementation, perform a separate review pass before declaring Phase 9 complete.

Review specifically for:

- tenant escape;
- permission confusion among read/resolve/manage;
- reply rows incorrectly counted as issues;
- N+1 provider calls;
- wrong asset/source hydration;
- resolve/reopen missing audit;
- frontend-only authorization;
- statistics counting foreign tenant data;
- optimistic UI stuck resolved after server rejection;
- mutation accidentally modifying guest content;
- mutation accidentally modifying provider metadata;
- historical migration edits;
- secret or annotation-content logging.

Fix only concrete Phase 9 regressions during this pass.

## 19. Completion gate

Phase 9 is complete only when:

- Review Board sidebar/route exists;
- list/detail/stats APIs are tenant scoped;
- RBAC split is enforced server-side;
- open/resolved/Done/Reopen workflow works;
- statistics obey counting rules;
- pagination and filters are bounded;
- audit events exist;
- required backend/frontend/security tests pass;
- existing Public Review tests remain passing;
- docs required by repository instructions are updated;
- completion report is produced;
- production has not been deployed as part of the phase.

## 20. Required completion report

Codex must report:

```text
Change summary
Files changed
Behavior changed
Security boundary affected
Invariants affected
Data migration yes/no
Permission/RBAC changes
New dependencies yes/no
Secrets/credential impact
Production permissions required
Tests added/updated
Tests actually run and results
Statistics/counting semantics verified
Known risks
Rollback procedure
Next recommended step
```

## 21. Suggested commit

Use repository conventions; an example is:

```text
feat(public-review): add internal review board and resolution workflow
```

## 22. Codex task prompt

A user may start this phase with the following minimal prompt:

```text
Implement Public Review Phase 9: Internal Review Board.

Read and obey:
- AGENTS.md
- docs/security/SECURITY_GOVERNANCE.md
- docs/architecture/AGENT.md
- docs/architecture/SECURITY.md
- docs/architecture/DATA_MODEL.md
- docs/architecture/PUBLIC_REVIEW.md
- docs/architecture/PUBLIC_REVIEW_BOARD.md
- docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md
- docs/plans/PUBLIC_REVIEW_REVIEW_BOARD_PHASE_09.md
- docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md

Implement only Phase 9.
Perform the required tests and independent review pass.
Return the mandatory completion report.
Do not deploy production.
```
