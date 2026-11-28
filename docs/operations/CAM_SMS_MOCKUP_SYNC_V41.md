# CAM_SMS_MOCKUP_SYNC_V41 — incremental target pictures

This release adds an operator-driven review path; CAM never calls SMS itself.

- Each current-master pack can be reviewed as soon as one original is saved. The old full-pack V4 readiness remains separately available.
- Verify only the next 1–6 original PNGs using the already-approved shared Drive root and exact SHA-256. Never use thumbnails. Originals below 2000 px are explicit opt-in via allow_low_resolution; they are not certified final-quality.
- Missing color metadata and missing target colorChoices are warnings, not image blockers. Only use the exact configured garment/target name; never infer a shade. The infographic is not color-tagged. Once the operator verifies metadata for an existing slot, plan a position-based color-only update if supported. Never overwrite an existing conflicting color.
- Gallery follows append order, min(9, actual photo count). No reordering, removing, replacing, template selection, price, SEO, video, reference-image edits, or new Idea creation.
- Existing receipt for the exact Idea and target is required. The V3 SMS Sync control records the Idea receipt with MANAGE permission.
- Read current SMS listing_ideas and every page of listing_idea_pictures (links_only). Normalize snapshot to files [{position,file}], gallery (integer or null), colors (position-to-name object or null), color_choices (actual live mockupPlan.colorChoices or []). Null is unobserved, never assumed verified. Do not copy the catalog template choices as if they were target choices.

## Operator API (authenticated MANAGE, same-site JSON)

GET listing-idea-preflight: sms_mockups_v41 includes available slots, below-2000 slots, missing color metadata and pending operation id/count. Use the actual pack review SHA.

POST /listings/{listing_key}/sms-mockups-incremental-prepare:
confirm=true, pack_id, garment_code, expected_pack_sha256, target_id, shop, snapshot, allow_low_resolution.
This reserves one exact local operation before any SMS write, using the existing Picked draft JSON, not a new table. It returns an operation_id and the exact update-only payload. Show all changes and obtain explicit SMS confirmation. No automatic SMS write.

After sending via authorized SMS MCP, read all photos and mockupPlan again. POST /listings/{listing_key}/sms-mockups-incremental-readback:
confirm=true, operation_id, snapshot, outcome=applied | partially_applied | not_applied.
Only report applied after confirming ordered correspondence to the reserved images. Partially_applied requires confirming the exact appended prefix and that no external write is still outstanding. Not_applied requires confirming no pending external write and an unchanged snapshot. A mismatched count, file identity, pack/master or untracked existing picture list blocks automatic continuation. Never clear a pending operation just to retry. SMS picture bytes are not independently certified by CAM.

If the SMS plan does not expose gallery or colors, readback reports the limitation. Resume only after fresh operator readback. The original V4 planner cannot be used on a target with V4.1 progress. No quota bypass.
