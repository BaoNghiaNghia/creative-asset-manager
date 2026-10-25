# GatorHats Products Stage 2 · Slot 7 front-stock synchronization

## Scope and evidence

Slot 7 generates **one front-facing product photo for each of 13 Valucap 8869
colorways**. It does **not** require or imply a full front/side/back synchronized
group. The other Stage 2 skills continue to use the existing shared
three-angle, versioned stock manifest without modification.

At the October 25, 2026 audit:
- 9/13 colors had 1254×1254 image fronts and all requested views in
  `gatorhats-hat-stock/manifest.json` (imported October 24).
- The other four (Khaki/Maroon, Natural/Black, Natural/Navy, Natural/Red)
  were still using 512×640 older bundled fronts.
- The supplier product variant page at
  `https://www.wueinc.com/products/valucap-five-panel-twill-cap-8869`
  identifies the four exact Valucap 8869 colors. Its front-only high-resolution
  photos are 1500×1500 and have pinned SHA-256 values in
  `apps/api/app/modules/realistic_review_ugc/slot7_front_stock.py`.
- 13/13 prior colorway outputs for one embroidery design were completed
  October 23. **Do not erase or wholesale replay these outputs.**

## Import (explicit, audited)

Both utilities default to no database or stock writes. Execute from the
deployed release with its approved Python environment and production config.

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_slot7_stock_sync.py
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_slot7_stock_sync.py --apply
```

The importer fails closed unless the supplier's exact 8869 variant names,
HTTPS host, CDN path, 1500-pixel JPEGs and preapproved SHA-256 are all
verified. It commits the manifest atomically into persistent
`<CODEX_IMAGE_HOME>/gatorhats-hat-stock/slot7-fronts`. The shared
`manifest.json` is not altered; full-angle groups will always take priority.
A later complete 3-angle import for one of the four colors automatically
takes precedence over its Slot 7 front-only photo.

The 4 supplier fronts are product photography from an external seller:
review applicable image reuse rights before publishing commercial assets
outside the application's internal product-mockup workflow.

## Reconcile only stale completed outputs

Use the already-authorized production PostgreSQL runtime identity and
`DATABASE_URL`; never log passwords or dump service environment files.
The reconciliation command **never reruns other Stage 2 slots**, never changes
source revisions, and never creates jobs for the other 36 designs.

```bash
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_slot7_stock_reconcile.py
PYTHONPATH=apps/api apps/api/.venv/bin/python scripts/cam_slot7_stock_reconcile.py --apply --max-jobs 3
```

Repeat the bounded apply, checking worker health, until the dry-run reports
zero stale **completed** jobs. Only a stale row with a verified current
13-color stock source and preserved original output is eligible. The existing
logical job is moved from completed to queued with the *new* front-stock SHA.
The normal processing repository creates the new attempt and retains tenant
priority/policy/fairness. Retries never silently change the pinned SHA.

On success, `save_output_version` records the updated image as the next
immutable output version. The existing completed file remains accessible
through its previous version; do not delete it or overwrite its stored object.

## Recovery and verification

- Check `GET /api/v1/realistic-review-ugc/colorways/readiness` for 13/13
  healthy stock references (authorization required).
- Check Slot 7 new per-color output URLs and version history after jobs complete.
- Verify the new stock SHA equals the job's pinned `stock_sha256`, and
  old output versions are still present in `rrugc_image_output_versions`.
- Revert the application release via the established `current/previous`
  mechanism if CI or API/worker health checks fail. **Never delete** managed
  stock, job records, old immutable versions or supplier front revision folders.
- A corrupted or missing previously configured supplier photo blocks the
  colorway rather than silently reverting to a mismatched bundled fallback.
