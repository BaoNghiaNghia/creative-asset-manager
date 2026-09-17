# Public Review Portal — Codex Runbook

**Status:** ACTIVE EXECUTION GUIDE  
**Architecture:** `docs/architecture/PUBLIC_REVIEW.md`  
**Phase plan:** `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`  
**Last updated:** 2026-09-17

This document defines how Codex or another coding agent must execute Public Review work. It is intentionally procedural. The architecture document decides what the system must be; the implementation plan decides phase scope; this runbook decides how an agent should work safely inside each phase.

## 1. Mandatory reading order

Before every Public Review task, read in this order:

1. `AGENTS.md`
2. `docs/security/SECURITY_GOVERNANCE.md`
3. `docs/architecture/AGENT.md`
4. `docs/architecture/SECURITY.md`
5. `docs/architecture/PUBLIC_REVIEW.md`
6. `docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md`
7. this file
8. implementation-specific docs such as `DATA_MODEL.md`, provider docs, or deployment docs as required by the current phase

Do not begin from a remembered chat summary. The repository docs are authoritative.

## 2. Core operating rules

Codex must follow these rules for this feature:

- Work on exactly one phase at a time.
- Inspect current code before editing.
- Do not assume file paths or APIs still match a previous conversation.
- Preserve tenant/source/object authorization server-side.
- Preserve explicit tenant predicates in repositories.
- Do not duplicate existing folder ancestry logic if it can be safely refactored/reused.
- Do not weaken ViewerFolderScope behavior.
- Do not fabricate a CAM user/membership for public reviewers.
- Do not store raw share secrets or public session values.
- Do not expose provider credentials, signed URLs, or local paths to the public client.
- Do not introduce realtime collaboration unless the architecture doc is updated first.
- Do not deploy to production as part of implementation phases.
- Do not execute destructive production migrations.
- Do not disable security tests or CI controls to get green output.
- Do not silently expand a phase because later-phase work is convenient.

## 3. Start-of-phase protocol

At the start of each phase, Codex should produce an internal working checklist containing:

```text
Current phase number/name
Current branch
Current commit SHA
Files/modules expected to inspect
Security boundaries touched
Migration required? yes/no
Dependency change expected? yes/no
Relevant existing tests
Baseline failures already present before changes
```

If the baseline is red, record exact existing failures before editing. Do not attribute an existing failure to the new phase.

## 4. Required implementation loop

Use this loop:

```text
read docs
  -> inspect implementation/tests
  -> state invariants affected
  -> make smallest coherent change
  -> add happy-path tests
  -> add negative/failure-path tests
  -> run focused checks
  -> inspect diff
  -> run broader relevant checks
  -> independent review pass
  -> fix concrete findings
  -> completion report
```

Prefer small commits and small diffs.

## 5. Independent review pass

After implementation, perform a second pass as if reviewing code written by another engineer.

Use this review instruction:

```text
Review the Public Review phase implementation as if you did not write it.

Do not add new product features.

Compare the code against:
- AGENTS.md
- SECURITY_GOVERNANCE.md
- SECURITY.md
- PUBLIC_REVIEW.md
- PUBLIC_REVIEW_IMPLEMENTATION.md

Inspect specifically for:
- tenant escape
- source/folder-scope bypass
- canonical asset vs source-asset confusion
- provider ID accepted as internal asset ID
- authorization performed only in frontend
- missing explicit tenant predicates
- fail-open behavior when hierarchy/cache/provider data is missing
- raw share key/session leakage
- provider credential or signed URL leakage
- CSRF/origin mistakes
- revocation/rotation/cache bypass
- annotation ownership bypass
- unsafe rich-text rendering
- unsafe migration behavior
- missing negative tests
- unrelated behavior changes

Run relevant tests.
Fix only concrete regressions/security violations found in this review.
Do not begin the next phase.
```

## 6. Phase-specific agent instructions

### Phase 1 — Data foundation

Agent focus:

```text
models
repositories
schemas/services needed for persistence
new Alembic migration
DATA_MODEL documentation updates after implementation
unit/migration tests
```

Do not add:

```text
public routes
frontend
Tiptap
share management UI
```

Before completing, verify raw secret/session values are not present in model columns or fixtures.

### Phase 2 — Authorization

Agent focus:

```text
existing ViewerFolderScopeService behavior
shared ancestry resolver extraction/refactor
SharePrincipal
share-scope authorization service
negative authorization tests
```

Do not copy/paste the hierarchy traversal into a second independent implementation.

Any refactor of existing viewer authorization must have regression tests proving viewer behavior is unchanged.

### Phase 3 — Internal management API

Agent focus:

```text
public_review.manage permission
CurrentPrincipal integration
share CRUD/revoke/rotate
scope validation
audit events
one-time raw secret return
```

Verify normal GET/list endpoints cannot reconstruct or return old secrets.

### Phase 4 — Public read boundary

Agent focus:

```text
fragment-secret exchange endpoint
opaque server-backed public session
cookie security
SharePrincipal resolution
folder/asset read routes
thumbnail/preview authorization
rate limits
security/cache headers
```

The raw fragment secret must not become part of normal route paths or logs.

### Phase 5 — Annotations

Agent focus:

```text
guest identity
annotation CRUD
owner checks
allow_comments
rich-text JSON validation
plain-text extraction
origin/CSRF protection
write rate limiting
```

Never trust client `plain_text` or raw HTML.

### Phase 6 — Public frontend MVP

Agent focus:

```text
separate public-review frontend directory
/share/:publicShareId route
fragment exchange/remove
folder explorer
asset grid/viewer
review sidebar
guest dialog
plain editor adapter
states/accessibility/tests
```

Do not reuse authenticated data hooks simply because UI components look similar.

### Phase 7 — Tiptap + pins

Agent focus:

```text
minimal Tiptap dependencies
allowed schema only
slash menu
safe links
normalized image pin utility
responsive pin rendering
frontend tests
```

Do not add Yjs/WebSocket collaboration.

### Phase 8 — Internal UI + hardening

Agent focus:

```text
Asset Explorer Share for review action
management dialog/page
permission-aware visibility
rotate/revoke confirmations
end-to-end security matrix
broad regression tests
docs/ROADMAP/REVIEW updates
```

Phase 8 completion is not deployment approval.

## 7. Test discipline

### Backend

Use the smallest relevant suite first, then broader suites required by the phase.

Typical categories:

```text
public_review unit tests
authorization tests
explorer tests
auth/RBAC tests
migration one-head test
PostgreSQL migration/repository integration
SQLite compatibility tests
```

If a shared search/pipeline/provider module is changed, run its relevant integration suite too.

### Frontend

Required when frontend is touched:

```text
npm test
npm run typecheck
npm run build
```

Also satisfy the repository's committed `dist` reproducibility requirement when applicable. Do not hide generated-dist diffs.

### Never claim unrun tests

Completion reports must distinguish:

```text
PASS — actually executed and passed
FAIL — actually executed and failed
NOT RUN — not executed
BLOCKED — could not execute, with exact reason
```

## 8. Secret-handling checklist

Before finishing any phase touching public sessions/links, search the diff and relevant outputs for accidental exposure of:

```text
raw share key
#key= values
raw public share-session cookie
session token fixtures that resemble real credentials
provider OAuth token
provider refresh token
signed provider URL
Authorization header
local protected path
```

Test fixtures must use clearly fake values.

Logs and audit records should use bounded IDs/digests/prefixes only when necessary.

## 9. Migration checklist

For any Public Review migration:

```text
current Alembic head inspected
new revision depends on current head
one head after migration
upgrade tested
repository/model tests tested
downgrade tested where supported
re-upgrade tested
PostgreSQL tested
SQLite compatibility considered/tested
no historical migration edited
rollback notes documented
```

Do not execute production migration from normal Codex implementation work.

## 10. Authorization checklist

Before completing Phases 2–8 verify, as relevant:

```text
explicit tenant predicate present
source belongs to tenant
folder belongs to source
selected root allowed
descendant allowed
sibling denied
other root denied
foreign tenant denied
wrong source for same canonical asset denied
provider ID cannot impersonate internal asset ID
missing ancestry data fails closed
media repeats auth
annotation repeats auth
revocation invalidates session behavior
rotation invalidates old credentials
```

## 11. Frontend security checklist

For public frontend changes:

```text
no raw HTML injection
no provider signed URL construction
no localStorage/sessionStorage persistence of share key
fragment removed after exchange
no internal CAM navigation
no authenticated hook assumptions
unsafe actions require server response
invalid/revoked/expired states are generic
links use safe protocol handling
```

## 12. Performance checklist

Performance optimization is allowed only after authorization correctness.

Do not:

- replace deny-on-missing data with allow;
- bypass server authorization to reduce calls;
- ship the entire tenant tree to the public browser;
- perform unbounded annotation/folder listing;
- introduce per-result provider calls when safe local ancestry data already exists.

When caching authorization-derived results, define invalidation behavior for:

```text
scope update
share revocation
secret rotation
provider/source reconciliation
folder hierarchy changes
```

## 13. Documentation change protocol

If implementation discovers that the approved architecture is impractical or unsafe:

1. stop feature implementation for the affected decision;
2. document the issue;
3. propose the new contract;
4. update `docs/architecture/PUBLIC_REVIEW.md` and implementation plan;
5. review the security effect;
6. then resume code work.

Do not implement architecture drift first and document it later.

Minor implementation details that do not change behavior/security contracts do not require architecture changes.

## 14. Required completion report template

Use exactly this information, though formatting may vary:

```text
Phase:
Current commit SHA:

Change summary:

Files changed:

Behavior changed:

Security boundary affected:

Invariants affected:

Data migration:
- yes/no
- revision if applicable
- downgrade/rollback notes

New dependencies:
- yes/no
- packages if applicable

Secrets/credentials impact:

Production permissions required:

Tests added/updated:

Tests actually run:
- command/suite: PASS/FAIL/NOT RUN/BLOCKED

Existing baseline failures still present:

Known risks:

Rollback procedure:

Independent review findings:

Next phase readiness:
- READY / NOT READY
- reason
```

## 15. Stop conditions

Codex must stop the current phase and report rather than silently improvising when any of these occur:

- current implementation contradicts a core identity/security contract in `PUBLIC_REVIEW.md`;
- a migration would require destructive data change not approved by the docs;
- safe share authorization appears to require weakening current tenant/provider boundaries;
- secret handling would require exposing provider credentials to the browser;
- required tests demonstrate an unresolved cross-tenant/scope bypass;
- phase requires a production secret or production destructive action;
- implementation would require realtime collaboration not in MVP;
- an architectural decision must change materially.

Stopping means returning a concrete technical finding and proposed documentation change, not simply abandoning the task.

## 16. Recommended phase invocation template

For future Codex runs, the human can use a short instruction because this runbook is authoritative:

```text
Implement Public Review Phase <N> from:
- docs/architecture/PUBLIC_REVIEW.md
- docs/plans/PUBLIC_REVIEW_IMPLEMENTATION.md
- docs/plans/PUBLIC_REVIEW_CODEX_RUNBOOK.md

Read and obey AGENTS.md and the required security/architecture docs first.
Work only on Phase <N>.
Run the required tests, perform the independent review pass, and return the mandatory completion report.
Do not deploy production and do not begin the next phase.
```

No large copied prompt from chat is required after these repository docs are present.

## 17. Definition of done for the entire feature

The feature is implementation-complete only after Phase 8 when:

- all architecture-required tables/routes/UI behavior exist;
- public and internal authorization matrices pass;
- public session exchange avoids raw secret in normal HTTP path/query;
- exact asset/source scope is enforced;
- public media cannot bypass authorization;
- annotation ownership/rich-text validation is enforced;
- Tiptap editor and normalized pins work;
- internal share management works;
- existing viewer folder scope behavior remains intact;
- relevant CI is green or any unrelated baseline failure is explicitly documented;
- docs match implementation;
- rollback is documented;
- no production deployment has been performed without a separate explicit authorization.
