# Branding DNA — Evidence Assessment Ledger (phase 1)

This phase adds a **tenant-scoped, append-only human assessment history** for
metadata-only listings. It is intentionally separate from independent
provenance verification and copyright/license clearance.

## Operator flow

In **Branding DNA → Product Explorer**, choose a listing, inspect its gallery
metadata, then complete **Đánh giá hồ sơ nguồn và quyền ảnh**. Another user
than the original metadata importer must perform this review. The review
captures a metadata decision, an image-rights-paperwork **basis** and a note.
When a reference to consent paperwork is recorded, enter its document ID,
not raw credentials, sensitive secrets, private storage URLs or media bytes.

API:
- `GET /api/v1/branding-dna/shops/{shop_id}/listings/{snapshot_id}/assessments`
- `POST /api/v1/branding-dna/shops/{shop_id}/listings/{snapshot_id}/assessments`

Both endpoints require `ai_provider.configure`. Rows are tenant/shop/snapshot
scoped; reviews are append-only and idempotent for repeated identical submissions.
The database constrains decision and rights-basis values. CI verifies additive
migration `0167`, and prevents deleting review history on production databases.

## Critical limits

- `metadata_consistent` is a human check, **not** independent listing verification.
- `consent_reference_recorded` acknowledges a document identifier, **not**
  confirmation that a license is valid or sufficiently scoped to a specific image.
- The feature does not modify `bc_listing_snapshots.evidence_state`,
  `bc_listing_images.rights_state`, staged scan jobs or marketplace access.
- Brand DNA Draft preview continues to use independently verified listing rows
  only. It does not use these assessments as an automatic verification source.
- Amazon/Etsy Browser Agent dispatch remains disabled pending separate proof
  and authorization requirements.

An actual independent evidence adapter, media bytes verification, license scope
validation, reviewer authorization and a separate approval gate must be designed
and tested before any automatic upgrade of evidence or media rights.

Production rollout is contingent upon a successful isolated backup/restore
rehearsal and CI passing for the exact release commit.
