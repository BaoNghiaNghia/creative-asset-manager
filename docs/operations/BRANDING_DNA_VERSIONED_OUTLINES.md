# Branding DNA — Versioned, citation-backed fact outlines

This phase adds **immutable evidence outline snapshots** and a two-person,
append-only documentary review. It does **not** provide a generated Brand DNA,
a final approval state, an image rights license, or a marketplace scan grant.

## Flow

1. In **Brand DNA → DNA Readiness**, choose a shop and click the existing
   **Tạo bản xem trước (read-only)**.
2. An outline can be saved only if the **server-side** preview is
   `evidence_outline` and has no blockers. Imported unverified listings are
   insufficient, and sample truncation also blocks creation.
3. Click **Lưu phiên bản từ preview**. The client submits only a 64-character
   input fingerprint. The server reconstructs the entire facts-only outline
   from trusted database reads, verifies freshness, and stores an immutable
   JSON snapshot, SHA-256, number and creator. Identical input fingerprints
   are idempotent per shop.
4. Later source updates cause the old version to show **stale**. An attempt to
   save using an old fingerprint returns 409. Existing snapshots are never
   overwritten, and reviewers cannot review stale versions.
5. A distinct operator may mark an outline `needs_changes`,
   `fact_outline_reviewed` or `rejected`. These entries are append-only;
   `fact_outline_reviewed` only means the factual outline was checked.
   It **does not** publish or authorize a Brand DNA.

API (requires `ai_provider.configure` and exact tenant/shop scope):
- `GET /api/v1/branding-dna/shops/{shop_id}/dna-draft/versions`
- `POST /api/v1/branding-dna/shops/{shop_id}/dna-draft/versions`
- `GET /api/v1/branding-dna/shops/{shop_id}/dna-draft/versions/{version_id}`
- `POST /api/v1/branding-dna/shops/{shop_id}/dna-draft/versions/{version_id}/reviews`

Safety guarantees:
- Only server-issued verified-listing citations and approved RAG reference
  metadata are snapshotted, explicitly separated by trust tier.
- Image rights, visual style, bestseller, Brand Pick, demographic and sales
  claims remain unresolved. No model is called.
- Reviews do not modify product evidence, media rights, scan tasks or
  marketplace access.
- Version and review history is append-only and protected against production
  downgrade. Migration: `0168_branding_dna_outline_versions`.
- The UI displays at most 25 recent versions; the API counts all versions.
- CI verifies local SQLite rehearsal, PostgreSQL upgrade and bounded rollback
  on a disposable integration database only.

Production deployment remains blocked without a verified isolated
backup/restore rehearsal, exact-commit CI success and rollout preflight.
