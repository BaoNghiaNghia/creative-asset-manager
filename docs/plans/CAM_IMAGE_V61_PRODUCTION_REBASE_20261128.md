# CAM Image V6.1 production rebase — 2026-11-28

The live CAM release f9f31c0911b060092ca0bef47bf52dfeff73d1cb already
applied 0217_listing_image_sms_color. The prior 41cd90aa image release
was built but never activated and has been removed by another deployment.
Do not target or re-deploy 41cd90aa.

Image candidate is rebased onto f9f31c091. None of its four image-domain
migrations were applied to production. Renumber the unpublished revisions:
- 0217_listing_image_sms_color (live, unchanged)
- 0218_cam_image_orchestrator
- 0219_cam_image_commands
- 0220_cam_image_approvals
- 0221_cam_image_qa_records

Single Alembic head: 0221_cam_image_qa_records. All image migrations
remain additive. Rehearsal scripts now expect the new source revision and
11 new tables; earlier synthetic results are historical, not proof for
this chain or the live database. No staging database or migration is run
as part of this rebase.

The recent managed backup receipt is PASS, but the latest observed backup
predates the SMS migration. No independent off-host restore proof is
available; the VPS does not have the free space for a same-volume restore.
Keep the existing recovery gate. The database backup runbook and
CAM_IMAGE_P2D_RESTORED_CLONE_OPERATOR apply with the updated revisions.
Do not run a production restore or manufacture the required source snapshot.

After recovery evidence and release preflight pass, deploy directly to
production using the normal native release script and retain the prior
release. Check the latest active release and Alembic revision first;
stop and rebase if another deployment has advanced them. Keep image claim
and renderer disabled until the actual adapters, image-worker credential,
legacy extension cutover and storage evidence are verified.

Muse runtime bundle is unchanged:
3e597b830f29f4fec6762f4d9f6ebd1d251baffa5b608a186297b5ab65c9fb88.
Do not require a fixed, obsolete CAM commit in the Muse cron; use CAM
server-reported live protocol, schema and exact runner pins plus execution
permission. A missing release/schema field or a false permission is not
readiness. Do not start a second image cron or modify video workers.
