# CAM Etsy Spy — Picked multi-Design mapping and historical backfill
**Date:** 2026-11-10 · **Tenant:** creative-assets-prod · **Environment:** production CAM + DESKTOP-3FR0LF0.

## User-facing behavior

The Picked product table has a **Design** column directly after Product / Etsy. Each row may have zero or many thumbnails. On click, a responsive gallery shows all versions, position labels (front, back, left/right chest, left/right sleeve, neck, hem, other, unknown), source method, job ID and a private original-PNG link. Preview is never called a finished print file.

Existing Fast Extraction receipts (status `needs_review`) and historical review candidates (status `needs_ai`, `review_candidate`) are automatically projected from their immutable ProcessingJob payloads. A candidate with no verified garment position is marked **Chưa xác định**, never guessed to be front/back. A valid tenant/READ session and an actively Picked listing are required for both original and thumbnail access.

## Multiple source/output mapping

New exports can be attached by the paired, **originally claiming** Windows Image Agent using `POST /api/v1/etsy-spy/image-agents/{agent_id}/designs` with:
- the completed job UUID, exact original `source_sha256`, PNG hash and RGBA 4500×5400 bytes;
- `position` from the known garment-side enumeration (or `unknown`), `position_verified=true` when a known position is claimed;
- immutable `variant` in 1..12, `method` in `fast_cpu`, `local_layered`, `imagegen`, `manual`;
- `print_ready=false`, `rights_verified=false`, `ocr_verified=false`.

Each output is stored in the same private tenant-hashed extraction storage. `ProcessingJob.payload_json.design_assets[]` is a bounded, persisted, one-to-many registry: generated artwork maps to the Etsy listing, job, original source SHA256, garment position, variant, PNG SHA256, method, review status and API URLs. This avoids a new database migration and does not alter existing Scout/agents. For the original Etsy product image, no separate back/sleeve photo is inferred or generated.

All uploads are idempotent for the same slot/variant/hash; different outputs cannot silently overwrite a prior slot. Images are stored server-side, not fetched from arbitrary external URLs. Thumbnails are generated on demand (192px RGBA) by Pillow; the full 4500×5400 PNG is fetched only when the gallery is opened.

## Queue-only backfill

The previous `POST /extractions/backfill` enforced one pending pilot row when `CAM_ETSY_EXTRACTION_DAILY_CAP=1`. New `queue_only:true` explicitly queues up to 200 **missing** Picked listing jobs per operator request without changing the **claim** cap. All records are selected from current eligible listings with a verified Etsy CDN source; existing jobs, cancelled generations, blacklist and Custom exclusions continue to use the existing idempotent policy.

Use the private host-only operational script (dry run default):
```
cd /opt/creative-asset-manager
PYTHONPATH=current/apps/api current/apps/api/.venv/bin/python current/scripts/cam-etsy-design-backfill.py --tenant-id creative-assets-prod
PYTHONPATH=current/apps/api current/apps/api/.venv/bin/python current/scripts/cam-etsy-design-backfill.py --tenant-id creative-assets-prod --apply
```
The script validates production Postgres/storage and calls the same queue selector as the authenticated API. It creates database jobs only; it never contacts Etsy, starts Windows Agent, enables a paid AI provider, or changes the daily claim cap. Backfill is safe to repeat.

## Rollout verification

1. Run 55 Etsy backend tests plus frontend EtsySpyPage tests/typecheck and production build validation.
2. Deploy API before UI. Verify previous single-case `review_candidate` is projected as one image, original receipt remains `completed/needs_ai`, and unauthenticated design endpoints reject access.
3. Backup relevant production processing jobs before backfill; dry-run and apply queue-only script. Count Picked, pending/processing/completed, missing jobs and new queue rows.
4. Deploy frontend using immutable commit and keep current+rollback. Verify JS, build-info and `/ready`.
5. Start Windows jobs only within a deliberately controlled daily claim budget and record honest `needs_review` or `needs_ai` results; this phase is **not** equivalent to approved PNG inventory.
