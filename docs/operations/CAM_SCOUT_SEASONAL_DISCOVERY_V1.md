# CAM Scout Seasonal Discovery V1 (2026-11-05)

## Goal

Find authentic customer-style candidate photos with seasonal surroundings matching
the ACTUAL source embroidery/design, not just generic sweatshirt/hat models.
Example: `embroidered-christmas-family-sweatshirt.jpg` should be searched with
Christmas terms, decorated Christmas trees, cozy family living rooms and visible
sweatshirt chest/sleeve areas.

## How it works

1. Existing source-image AI visual analysis is the evidence source. Reuse source
   filename, extracted embroidery wording/identity, identified artwork icons,
   occasion hints, scene hints and visual summary. No extra AI/embedding calls.
2. Deterministically detect a specific holiday or season. Supported: Christmas,
   Halloween, Thanksgiving, Valentine's Day, Easter, Fourth of July, New Year's,
   St Patrick's Day, winter, autumn, summer and spring. Specific holidays take
   precedence over general season when present in the same evidence field.
   Generic `holiday`, `family`, a snowflake, or the current calendar date
   cannot force Christmas. Ambiguous fields are not forced to an occasion.
3. For TikTokShop with ready apparel-placement evidence, send four seasonal
   search lanes to the Windows Scout. ALL four contain the
   detected season and garment; the primary lane keeps the front/sleeve-zone
   constraints. A stable primary lane plus rotating variants explores different
   surroundings over Scout attempts. `search_clusters.direct` and
   `search_queries` agree, replacing old generic legacy query hints only in
   the ephemeral claim payload. Source data and approved references remain intact.
4. For GatorHats Reviews source campaigns, build four season-specific people
   wearing caps, casual hat selfies, hand-held-hat, and family cap scenes.
   Persisted search queries remain unchanged; only the ephemeral claim is
   restricted to these four seasonal lanes. The standard context derivation
   also creates a seasonal hat anchor for newly analyzed sources.
5. Existing Gemini reference analysis gets an artwork-specific seasonal
   context target and scores the visible background/scene using
   `context_match_score`. Technical garment fit is scored separately;
   season alone never triggers a hard reject.
6. TikTokShop manual `(v)/(x)/(AI)` learning is isolated by
   `apparel:<family>:<season>` when a specific season exists, to avoid mixing
   feedback from Christmas and Halloween. Existing per-campaign feedback with
   prior `apparel:<family>` labels is still read only for the same campaign.

## Windows Scout v59 deployment contract

- Claims with an unambiguous source season include
  `source_context.seasonal_search` with `version=source-season-v1`,
  `season` and four `priority_queries`. The backend synchronizes this order
  after the per-family human-feedback rerank. Generic historical queries are
  excluded from the four claimed search lanes for BOTH workspaces.
- Scout v59 validates that each of the four priority queries is contained in
  the authenticated claim's `search_queries`. It executes exactly those four,
  skipping the legacy bandit reorder; invalid/incomplete contracts fall back to
  ordinary query selection. A compact `seasonal_search_claim_applied` diagnostic
  event records adoption without any credentials.
- **Compatibility:** v58 ignores the new contract, so the backend also sends
  only those same four season-matched queries in ephemeral
  `search_clusters.direct` and `search_queries`, with the other clusters
  emptied. This ensures source-season searches work immediately even before
  Windows updates to v59. Nonseasonal jobs are unaffected.
- Scout Manager Desktop v0.1.51 runs the Python launcher with `-SkipUpdate`;
  it performs Git updates itself only while **Run automation** is active,
  polling approximately once per minute. A managed source change on `main`
  triggers a safe fast-forward and restarts desired Scout modes. When paused,
  installed Scout code stays at its previous version until automation resumes
  or the existing standalone auto-update launcher is used. The Manager binary
  need not be rebuilt for v59; the updated `scout.py` is loaded at next launch.
- Confirm on Windows in **Details & diagnostics** that Scout version is
  `rrugc-scout-v59-seasonal-claim-priority` and the commit matches the published
  `main` revision. If an update is blocked by local Git changes/divergence,
  it stops rather than overwriting user files. Do not delete profiles, tokens,
  the Pinterest session or saved job history.
- Current reference images, approved picks and previous analyses remain in
  place. Seasonal searches affect the next claim, not previously accepted
  thumbnails; no bulk re-scout or destructive cleanup is part of this change.

## Non-goals and limits

- No browser-extension or desktop Scout reinstall, DB migration, destructive
  cleanup or paid provider requirement.
- Historical references are NOT automatically deleted/reclassified; new Scout
  claims and future candidate evaluations apply the policy. Photos can still
  be generic if Pinterest has few suitable results; the season is an optimized
  query and soft visual-context ranking signal, not a claim of guaranteed photos.
- Source design season and image background season are different concepts;
  the policy never infers protected personal traits from appearances.
- Season match remains conservative for ambiguous artwork. Explicit source
  cues override broad older `holiday` templates. In case of an unrecognized
  design, existing non-seasonal discovery remains the fallback.

## Verification / rollback

Tests include the screenshot Christmas family sweatshirt, icon-only holiday
evidence, holiday ambiguity, Halloween/Thanksgiving/Easter/Valentine and
other seasonal routes, query rotation, source-root separation, legacy feedback
compatibility and GatorHats hat composition.

Production is deployed from `main` with the native backend release script,
`--no-migrate` only after schema preflight, and 2 releases retained for rollback.
