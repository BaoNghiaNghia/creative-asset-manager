# CAM Branding DNA Handoff — production rollout safety gate

Current production status: **NOT CONFIRMED / DO NOT ASSUME DEPLOYED**.
This checklist supports an operator-approved deployment. It does not automatically deploy,
enable marketplace scans, grant rights, train models, or modify any production database.

## Change scope

- Source commits beginning with 92b56527; frontend build-info pins the tested source.
- Alembic revision 0162_branding_dna_handoff adds bc_handoff_candidates and one index.
- Routes preview, stage, list, review under /api/v1/branding-dna/shops/{shop_id}/handoffs.
- No direct browser-to-evidence import. Rights remain unknown.

## Must-pass gates, in order

1. GitHub Actions **CI** on the exact candidate SHA must have all required jobs green,
   including PostgreSQL 12 and 16 migration upgrade/downgrade/re-upgrade,
   API integration, Frontend, and fixture Browser QA. A green local test alone
   is insufficient. Record the workflow URL and candidate SHA.
2. Verify production database backup **actually exists and can be restored**.
   The existence of database_backup_cli.py does not prove a timer is enabled
   or that a recent backup completed. The older DATABASE_BACKUP.md document
   still says the production schedule is not activated. Operator must
   examine the database-backup systemd timer/service and the latest result,
   run the CLI verify-config and list commands using the authorized production
   managed-storage identity, check the remote backup checksum and expected
   PostgreSQL database identity, and perform or locate a documented
   successful restore rehearsal into an isolated database. Record backup
   file ID, timestamp, verified checksums and restore-test record privately.
   **No backup or no restore evidence = STOP.**
3. Verify production disk headroom, available memory, current Alembic
   revision, release symlinks, deployment lock and current API/worker health.
   The last observed VPS disk snapshot was 50GB total / 12GB free; that
   number does not prove sufficient disk and RAM at deployment time.
4. Re-run isolated local checks from the **exact** candidate commit:

       cd apps/api
       .venv/bin/python -m pytest tests/modules/branding_dna tests/migrations/test_branding_dna_handoff_migration.py -q
       .venv/bin/python -m alembic heads
       cd ../client
       npm run typecheck
       npm test -- --run app/branding-dna/BrandingDnaPage.test.tsx

   Expect exactly one Alembic head. Check that committed frontend
   dist/build-info.json build_commit is a verified ancestor of HEAD and
   no frontend source changed after the recorded build commit.
5. Only after gates 1–4 and with authorized production access, use
   the project's guarded existing deploy entrypoints:

       sudo scripts/cam-rebuild-backend.sh --commit <candidate-commit>
       sudo scripts/deploy-cam-frontend.sh --commit <candidate-commit>

   Do not add --allow-dirty. Do not use --no-migrate for this release.
   The backend script owns environment validation, disk and service checks.
   Stop immediately if any validation fails; do not hide failures with || true.
6. Post-deployment smoke:

   - /live, /ready, /version and /build-info.json return healthy/current values.
   - Production UI Branding DNA → Product Explorer has Handoff selector,
     no Start Scan and no Approved/Verified action.
   - Unauthenticated API access returns 401; read-only principal can Preview
     but cannot Stage (403); cross-tenant data is hidden (404).
   - A selected owned/authorized shop can Preview a deliberately unverified
     1-product test packet; Preview creates **zero** database rows.
   - Only with operator approval Stage one test item. Repeated Stage returns
     replayed=1 and created=0. Review can only select one of
     pending_source_review, needs_authorized_export, rejected.
   - Read-only browser/mobile/desktop QA at 390, 768, 1366 and 1920 pixels.
   - Other workers remain healthy; do not activate marketplaces or schedule scans.

## Rollback considerations

Revision 0162 deliberately blocks destructive production downgrade.
Its test-only opt-in is valid only for named disposable test databases, never
for production. Do not attempt forced Alembic downgrade to roll back the UI.
Use the existing frontend immutable release rollback for presentation issues.
For backend/database issues, preserve the database and pursue a separately
reviewed forward-fix or a tested disaster-recovery procedure.

## Known constraints

- Current CodeLocal workspace access is limited to /root/creative-asset-manager.
  Do not try to use an indirect script to bypass authorization for /opt, /etc,
  /var/lib, production credentials or backup files. An authorized operator or
  separately approved production workspace must perform the real deployment.
- Browser QA on the root VPS previously failed to launch Chromium due to the
  browser sandbox, not because the Branding DNA UI was tested and found bad.
  Use a supported non-root Browser QA runner.
- Keep the existing Amazon/Etsy pilot bounded, approved and manual; no
  automatic full-shop scanning or marketplace API dependency is introduced.
