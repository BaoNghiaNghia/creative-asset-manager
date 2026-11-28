# CAM Eligible design extraction V1

- Existing tenant-scoped Etsy Spy Eligible predicate is the authority for discovery: current, qualified, not blacklisted, not Custom, not Later.
- Existing Picked jobs retain priority. When no claimable job exists, the paired Image Agent claim materializes one eligible extraction and leases it through the same provider, quota, storage checks, executor fence and result routes. No new database or parallel queue.
- Picked promotion reuses the active/same-source Eligible job; no implicit Pick or TikTok SEO, image-generation, Muse or SMS operations.
- Eligible and Picked listings project the same immutable design artwork. Eligible status and Design column are read-only; manual design retry remains Picked-only.
- Extension's CAM queue projection includes eligible_available (distinct Eligible IDs without a noncancelled extraction; not a promise of source validity) and eligible_waiting (materialized jobs, already part of waiting). Do not add these to local browser job counts.
- Default on when extraction transport is enabled; rollback switch CAM_ETSY_ELIGIBLE_EXTRACTION_ENABLED=0 disables new Eligible admission. Existing eligible-origin jobs remain eligible for completion while still in scope. No migration.
