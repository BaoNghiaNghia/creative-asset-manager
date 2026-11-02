# CAM ThreadTransfer V1 - Products Stage 2 Slot 1

## Scope
- Only GatorHats Products, Stage 2, Slot 1 (prod_s2_ec1).
- Two persistent visible outputs: Embroidery Reference and Cap Front.
- Slot 2-7, Reviews, stock photos and immutable output versions are unchanged.

## Pipeline
1. Pin source design hash/revision, factory stock hash and placement snapshot.
2. Reuse the saved photographic reference when source SHA matches. If absent, generate exactly one native material reference with Codex ImageGen and checkpoint it before transfer.
3. Locally segment genuine yarn from transparent alpha or opaque fabric via border CIELAB color separation, texture and component checks. Ambiguous photos/flat prints fail closed.
4. Scale inside pinned field, shallow warp to convex cap crown, premultiplied alpha sample, adjust local light and apply bounded stitch-foot shadow.
5. Verify every stock pixel outside embroidery field is byte-identical; save PNG using existing output version/history, including thread layer SHA and transfer box.

## Retry
- Apply to Cap triggers first Slot 1 job. No custom ZIP Skill is used.
- Retry Placement reuses pinned Embroidery Reference, refreshes approved placement coordinates and performs zero native Codex calls/account leases.
- Existing completed front/version remains stored until new output passes validation.
- Source revision mismatch blocks incorrect reference reuse.
- Existing queued 2.2/2.3 jobs retain pinned old behavior; new 2.4 jobs use ThreadTransfer V1.
- Historical full regenerate backend endpoint remains for compatibility but is hidden in Slot 1 UI.

## Limitations and release
- Local raster compositing cannot fabricate absent fibers and cannot guarantee photographic sewing on every reference. Fail closed on low-confidence segmentation; manually validate representative real stock before bulk re-runs.
- Release both frontend and backend from main after appropriate checks with no database migration, retaining a rollback release.
- Do not bulk re-run old completed jobs or delete data, previous output versions or factory stock.
- The release needs no DB migration, new dependency, IAM, secret or network change.
