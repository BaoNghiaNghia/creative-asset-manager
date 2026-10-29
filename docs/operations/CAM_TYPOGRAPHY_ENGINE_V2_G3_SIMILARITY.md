# G3 Typography ROI near-duplicate advisory

**Status: code-only / requires CI; Typography V2 still OFF in production.**

## Design and cost

- The existing G3 analyzer already verified the original image SHA256, downsampled it, made one provider call and validated a normalized **typography-only region**.
- After successful analysis, `typography_dhash` crops that exact normalized region from the prepared image **locally** and produces a versioned `tdh1:<16 hex digits>` 64-bit horizontal difference hash. EXIF orientation and transparency are normalized; there is no second AI pass, internet request, image generation or source lettering extraction.
- The existing `rrugc_typography_style_dna.perceptual_hash` field stores the server-generated hash atomically with the immutable DNA result, under tenant/source/lease fencing. No new Alembic migration. If hashing fails, analysis can still complete without a hash, and the UI reports `hash_unavailable`.
- Valid same-version hashes can be compared by Hamming distance (0–64). A default distance of **6** is a **review hint only**. Similar backgrounds or glyph silhouettes may produce false positives; different crops, scales or typography regions can produce false negatives. Never treat a dHash match as legal/copyright/trademark proof, and NEVER automatically reject, train on or copy a reference from this signal alone.

## Read-only API

`GET /api/v1/realistic-review-ugc/typography/references/{reference_id}/dna/{dna_id}/similar?max_distance=6&limit=10`

- Requires `typography_reference.read`, existing V2 server/tenant canary and ownership/workspace visibility. Source DNA must match **current tenant, reference ID, image SHA and server-owned analyzer version**.
- Query scans at most 501 current-version, rights-verified, active candidate DNA rows **from the same tenant**. Non-manager users see only their own or workspace-shared references. Private refs of another user are filtered in SQL **before** the Hamming comparison; response never leaks even the existence of a private match.
- Return `status: ready | hash_unavailable | hash_stale_after_region_edit`, up to 25 match pairs `{reference_id, dna_id, distance}` (nearest first), and `scan_truncated` when more than 500 candidates exist. Exact hash bits, source URLs, assets, source text, usernames and AI payloads never appear in response.
- If a reviewer manually changes the typography rectangle, the hash tied to the original AI rectangle becomes stale: the source endpoint returns `hash_stale_after_region_edit` with no matches, and candidates with a corrected rectangle are excluded until explicitly rehashed locally. Never present a match computed against obsolete geometry.
- `max_distance` limited to 0–8, `limit` 1–25. The 500-candidate cap is a safety bound, **not** a production-scale completeness guarantee; any truncated scan must be manually reviewed and indexed before a larger rollout.

## QA and rollout

Synthetic-only tests cover identical ROI with different product backgrounds, inverse lettering gradient, transparent PNGs, corrupt images, invalid bounds, hash versioning, one-pass persistence, malformed hash rejection, privacy/RBAC/tenant and HTTP bounds.

This is a supplementary warning in G3 Human Review. An operator can select `needs_review/near_duplicate` using the existing auditable manual-review endpoint, while a false positive can be explicitly accepted. Eight rights-verified real images, manual comparison, false-positive count, ROI accuracy, latency and cost are still required for G3 signoff.

No production migration, backend deploy, automated worker or provider billing is performed by this change.
