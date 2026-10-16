# Branding DNA — Scout handoff verification queue (BD-02)

Status: Tenant-scoped manual verification intake. This is not Brand DNA evidence approval, marketplace scanning, training rights or browser-agent dispatch.

## Operator workflow

1. In Scout Manager v0.1.28 or later, run an authorized Amazon/Etsy pilot and Pick individual observations.
2. Click Prepare handoff. The manager creates a local handoff JSON under the Windows LocalAppData CreativeAssetManager/BrandingDNA/handoffs folder.
3. In CAM → Branding DNA → Product Explorer, select the registered shop for the active tenant, and choose the JSON under Handoff · Kiểm chứng nguồn.
4. Preview validates schema, platform, exact canonical shop URL hash, identifiers and explicit unverified flags. Preview never writes to the DB.
5. Stage requires ai_provider.configure and an owned/authorized shop. Each candidate is stored in bc_handoff_candidates with status pending_source_review.
6. Reviewer may select needs_authorized_export, rejected or pending_source_review. None of these status values assert rights or evidence verification.

## API

- POST /api/v1/branding-dna/shops/{shop_id}/handoffs/preview — read-only, ai_operations.read.
- POST /api/v1/branding-dna/shops/{shop_id}/handoffs/stage — explicit unverified staging, ai_provider.configure.
- GET /api/v1/branding-dna/shops/{shop_id}/handoffs — tenant/shop-scoped queue.
- PATCH /api/v1/branding-dna/shops/{shop_id}/handoffs/{candidate_id} — narrow review triage only.

The user cannot supply or override tenant identity. All queries are scoped to the authenticated active tenant. Duplicate sources are idempotent; conflicting content fails with HTTP 409.

## Critical limitations

- The original source hash is submitted by the operator, not an independently authenticated attestation. Exact matching verifies only the claimed shop identity and request structure, not seller truth or platform permission.
- No product image URL, image bytes, original artwork or media lineage accompanies the handoff. It cannot satisfy the separate authorized-export evidence-import API.
- Browser scans, task dispatch, production evidence commits and automatic training remain disabled.
- No state called verified, approved or ready for training is supported.
- Before any future promotion: reconcile a seller-authorized export; review platform access, legal rights and media lineage; establish provenance plus auditable actor approval.

## Safety gates

API tests: python -m pytest tests/modules/branding_dna tests/migrations/test_branding_dna_handoff_migration.py -q

Client tests: npm run typecheck, and vitest BrandingDnaPage.test.tsx

Migration: additive Alembic 0162_branding_dna_handoff. Production downgrade refuses to drop operator queue records. Only disposable CI PostgreSQL database `cam_integration` or isolated SQLite test file `handoff.db` can opt into destructive rollback using `CAM_ALLOW_HANDOFF_HISTORY_DROP_FOR_TEST=1`; the flag is scoped exclusively to the CI migration downgrade step. This preserves the CI upgrade/rollback/re-upgrade test without granting a generic production deletion route.

Do not deploy to production without a verified database backup, sufficient disk headroom, staging tests and authenticated browser QA.
