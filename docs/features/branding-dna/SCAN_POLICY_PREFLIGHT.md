# BD-SCAN-CALLBACKS-02B.2 — Shop-level policy preflight in Scan Readiness

Implemented: 2026-10-18. **Read-only; does not issue work or verify marketplace authorization.**

## Purpose

Make the manual RAG Policy review flow visible in **Branding DNA → Browser
Scanner → Điều kiện quét theo shop**, without falsely treating an internal
review as a provider grant. The read-only preflight shares the same validity
predicate with Shop Library's policy review records.

The `GET /api/v1/branding-dna/shops/{shop_id}/scan-readiness` response now
includes `policy_review`:
- `state`: `not_submitted`, `pending_human_review`,
  `current_human_review`, `revoked`, `expired_or_changed`, `rejected`,
  or `restricted` (non-admin view).
- `counts`: bounded summaries of the stored policy review states.
- `human_review_current`: true only if a human reviewer other than the
  submitter reviewed an approved, unchanged, unexpired policy document for
  the exact same tenant/shop/storefront/platform/policy scope.
- `marketplace_authorization_verified:false`,
  `dispatchable:false` always.

Every state maps to a specific blocker displayed in the Browser Scanner.
Even `current_human_review` adds a
`human_policy_review_not_provider_authorization` blocker. Independent
marketplace confirmation remains mandatory.

## Privacy and trust

The Shop Knowledge/Policy documents and review history are admin-only
(`ai_provider.configure`). Therefore a principal with only
`ai_operations.read` receives `state:"restricted"` and zeroed counts,
regardless of whether private documents actually exist. No document IDs,
reviewer identities, source text, SHA256, expiry dates or source URLs are
returned by this preflight.

Agent heartbeats are tenant-level *connectivity*, never proof of shop
assignment or platform permission. Snapshots remain
`imported_unverified` unless separately reviewed; staged jobs remain
staged. The response does not call browser code, change task status or
grant a token, lease, rights claim or verified product fact.

## Tests and release gate

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna -q
cd ../client
npm run typecheck
npm test -- --run app/branding-dna/BrandingDnaPage.test.tsx
```

Tests exercise an unchanged human-reviewed document, archive/revocation,
pending and expired documents, cross-tenant isolation, restricted read
permissions, and no job-dispatch changes. This feature has **no new DB
migration, secrets or external dependencies**. Rollback is code-only:
revert the shared preflight and the Scan Readiness UI; preserve existing
Policy/RAG data. Production deploy is still held until PR #24 CI and
database backup/restore, authorized marketplace pilot and Windows Browser QA
all meet the release gates.
