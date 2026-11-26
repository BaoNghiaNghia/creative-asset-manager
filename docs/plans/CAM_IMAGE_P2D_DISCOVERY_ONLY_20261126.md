# P2D discovery-only release — 2026-11-26

Current production base: 933918ca6. Full P2D candidate: 3c305f459 (116 passing tests).

This intermediate release has no new schema, migration, writer, scheduler, claim or renderer. Existing paired Muse/Image Agent authentication is reused. The three discovery endpoints return the candidate Skill manifest and PREPARED_DISABLED; central counts are explicitly unknown, and pack/QA reads return 503 until the central schema exists. Explicit CAM_IMAGE_READ_API_ENABLED=false disables discovery. Video readiness and worker behavior are unchanged.

G1 live preflight: backup receipt PASS, restore NOT_VERIFIED, same-volume restore capacity BLOCKED. Do not apply 0217–0219 or enable workers. Use the reviewed native backend deployment entrypoint with --no-migrate for this discovery-only release. Check current/previous release, API readiness, authenticated discovery and video/legacy behavior. No direct production overlay. Full P2D supersedes these bootstrap routes only after the independent recovery gate passes.

Verified: 33 targeted tests pass (discovery and existing video regressions). No production mutation during preparation.
