# BD-SCAN-CALLBACKS-02B.1 — Shop policy paperwork review (scan disabled)

Implemented 2026-10-18 on isolated feature branch. This stage does **not**
enable browser scanning, dispatch, source collection or production rights.

## Why a separate rights ledger

Self-declared `owned`/`authorized` shop registration is not proof a marketplace
permits automation, nor is an approved shop RAG document by itself. There is
a separately scoped, revocable **human paperwork review** record that can be
associated with an existing, reviewed `policy` RAG document.

- Etsy requires express **written** authorization to use browser automation
  on listing/shop data; merely owning an Etsy shop is insufficient.
- Amazon's 2026 Agent Policy imposes disclosure and possible restrictions;
  having an account does not authorize any autonomous agent to scrape.
- Separate permissions for image reuse, training, and making product claims
  are not inferred from a legal/technical access document.

This ledger records reviewer attestations, **not verified marketplace grants**.
Every HTTP response explicitly sets `marketplace_authorization_verified:false`,
`dispatchable:false`, and `scan_enabled:false`, including after human review.

## Endpoints

The CAM **Branding DNA → Shop Library** tab now contains a compact policy-review panel. It loads registered shops and their existing approved `policy` RAG documents from CAM; the Windows Agent UI remains agent-only and retains no shop configuration. The client refuses an unsafe server response that claims a scan is enabled.

All endpoints are tenant-scoped and require `ai_provider.configure`:
- `POST /api/v1/branding-dna/shops/{shop_id}/scan-policy-reviews` — body
  `{"document_id":"<approved policy RAG UUID>","expires_at":"<RFC3339 with offset>"}`.
  The document must belong to that shop and tenant, have category `policy` and
  status `approved`. Its original content SHA256 is captured. The expiry
  must be over 1 hour and at most 365 days ahead. At most 40 submissions
  are retained per shop; a document can only be submitted once.
- `GET /api/v1/branding-dna/shops/{shop_id}/scan-policy-reviews` — list
  snapshots of metadata with `human_review_current` calculated dynamically
  from the document revision/status, shop identity/access mode and expiry.
- `POST /api/v1/branding-dna/shops/{shop_id}/scan-policy-reviews/{review_id}/decision`
  — body `{"action":"acknowledge|reject|revoke","note":"..."}`.
  Requires a material note; acknowledgement and rejection require a different
  user from the submitter. A reviewer must inspect the separately stored
  original RAG document before recording a decision.
  A revocation preserves the original reviewer identity and adds
  `revoked_by`, `revoked_at`, `revocation_note`.

`human_review_current` is only true for a human-reviewed, unexpired review
of a still-approved unchanged policy document associated with the same current
shop identity. It does **not** mean `automated_scan_permitted` and never
unblocks the existing `/workers/{agent_id}/lease` no-job endpoint.

## Schema / data boundary

Migration `0166_bdna_scan_authorization_reviews` is additive to head
`0165_bdna_scan_failure_receipts`. It stores a bounded, document-linked
ledger; no document bytes, shop login secrets or URLs. Its tenant/shop/document
composite FK prevents cross-tenant references. The storefront fingerprint
SHA256 includes platform, region, canonical key and storefront URL, preventing
silently reusing a review for another shop identity.

Production downgrade is blocked to preserve audit history. GitHub PostgreSQL
CI now checks head 0166 and exercises isolated, explicitly flagged disposable
downgrade of 0166 to 0165, 0165 to 0164, then the older RAG/handoff rollback,
followed by an upgrade to 0166. No force-migrations on production.

## Still required to enable scanning

1. An independent verification step for a genuine, provider-issued written
   Etsy grant or Amazon Agent Policy compliance, legal scope, exact shop
   identity, User-Agent, data rights and expiry; maintain a revocable
   permission gate separate from human paperwork review.
2. A signed, tenant/worker/shop-scoped bounded scan job envelope; policy/robots
   check at execution time; global and per-shop kill switches; no arbitrary URLs.
3. Authenticated source evidence with immutable revisions and idempotent result
   receipts, media rights review and dedicated Windows browser tests.
4. Database backup/restore, CI, production schema preflight, canary and rollback
   verification. Never deploy changed API against a database below its new head.

## Tests

```bash
cd apps/api
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_authorization.py -q
.venv/bin/python -m pytest tests/modules/branding_dna/test_scan_authorization_migration.py -q
.venv/bin/python -m alembic heads
```

Negative tests include cross-tenant and cross-shop access, invalid/archived
documents, lack of written policy record, expired review, self-approval,
duplicate source submission, legal metadata change, preserved revocation
history, and destructive-downgrade refusal. No real marketplace browsing.

Policy documentation (reconfirm before production pilot):
- https://www.etsy.com/legal/api/
- https://www.etsy.com/legal/terms-of-use/
- https://sellercentral.amazon.com/help/hub/reference/external/GS83KH2MA7HM69PH
