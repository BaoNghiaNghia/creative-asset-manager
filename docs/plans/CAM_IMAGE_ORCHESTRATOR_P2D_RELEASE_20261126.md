# CAM Image Orchestrator P2D — release candidate, 2026-11-26

Base: production 933918ca6 (Muse Bridge 1.4.27). Scope: image-only authenticated discovery; no image execution and no video policy or cron changes.

- Integrate existing P2C modules and additive migrations 0217–0219.
- Discovery defaults on in this candidate, with explicit CAM_IMAGE_READ_API_ENABLED=false rollback. Claims, renderer and upload adapters stay disabled; discovery is not authoritative for legacy backlog.
- C1717 front-only V3 placement validation shares the exact Extension SHA-lock; advisory width cannot override it. Unknown size and true SMS width limit violations fail closed. 191.16 mm vs advisory 169.34 mm fixture matches Extension SHA 4e093b15b7913630ed3167e5d5fec8f96350476f41582cd849242b232b5852f2.
- Preserve the production slot coverage SQL, typed NULLs and existing legacy gallery paths. New central filenames are pack-scoped.
- QA and 2000px publication gates remain; no live plan, QA, palette, resource or Drive verification is claimed.

Verified: 110 targeted pytest tests (including exact JS hash, legacy gallery and Muse regressions), 6 real isolated PostgreSQL 16.4 contention tests (migrations 0217–0219, ten tables), Python compile checks and single Alembic head 0219. Test container removed. No production migrations, restart, claim, renderer, canary or video change.

Release gate: current and previous production releases exist, backup service last result success, but full independent backup restoration evidence has not been verified. Do not apply production migrations until that gate and release preflight are cleared. Use the documented native backend release script, not a direct production overlay. After release, verify authenticated discovery 200, imageClaimAllowed=false, legacy slot coverage and video readiness unchanged. Do not enable execution until legacy drain, exact source/receipt reconciliation, trusted QA, live resource arbitration and Drive verification pass.

## Follow-up: full-chain rehearsal and production discovery

Production is running discovery-only commit 8a7b9c0f7 with rollback 933918ca6. A fresh read-only G1 preflight returned backup receipt PASS, independent remote re-download NOT_VERIFIED, isolated restore NOT_VERIFIED, same-volume capacity BLOCKED (4,167,204,864 bytes free; additional estimate 7,562,086,947 bytes plus 8 GiB reserve). No production schema changes. Keep production discovery-only and Muse PREPARED_DISABLED.

Synthetic full-chain PostgreSQL 16.4 rehearsal in a fresh disposable container: Alembic base -> 0216 created 230 tables. Upgrade 0216 -> 0219 created the exact 10 image-domain tables, all empty; unchanged column and primary-key signatures of the 230 original tables, synthetic sentinel value preserved. Container removed. This tests the complete migration chain, **not** the production backup or actual restored rows. Run scripts/image_orchestrator/p2d_full_chain_rehearsal.py only via the isolated test wrapper.

Read-only Windows authenticated Discovery smoke: registry, commands and work HTTP 200, correct 19-file Skill manifest, claim/renderer=false. Legacy slot coverage sampled 361 Picked, 685 saved images, 3,717 missing roles, 1,850 already planned, 9 metadata-eligible for existing daily repair. 1,858 review roles. These are not central admission or QA evidence; no queue mutation by the audit. Windows Bridge was restarted using its previous single-lane configuration after a no-listener preflight; all 1,790 job IDs and six unknown attempts preserved. Extension reports connected and Auto enabled, but next actual render has not been verified.

Remaining operator gate: approved isolated off-host restore host, independent latest managed backup download and digest verification, complete PostgreSQL restore, documented source-snapshot count parity including critical protected tables, and 0216 -> 0219 rehearsal against the restored clone. Use the existing DATABASE_BACKUP and guarded restore runbooks. Do not export credentials or raw backup into chat, use the live VPS volume for rehearsal, fabricate a receipt, or skip the recovery gate. Full release remains disabled until these gates pass; central admission, trusted QA, live storage, legacy drain and canary are additional independent gates.
