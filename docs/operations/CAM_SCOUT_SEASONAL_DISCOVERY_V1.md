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
   search lanes to the existing Windows v58 runner. ALL four contain the
   detected season and garment; the primary lane keeps the front/sleeve-zone
   constraints. A stable primary lane plus rotating variants explores different
   surroundings over Scout attempts. `search_clusters.direct` and
   `search_queries` agree, replacing old generic legacy query hints only in
   the ephemeral claim payload. Source data and approved references remain intact.
4. For GatorHats Reviews source campaigns, prepend season-specific people
   wearing caps, casual hat selfies, and hand-held-hat scenes into ephemeral
   claim clusters without erasing older search queries. The standard context
   derivation also creates a seasonal hat anchor for newly analyzed sources.
5. Existing Gemini reference analysis gets an artwork-specific seasonal
   context target and scores the visible background/scene using
   `context_match_score`. Technical garment fit is scored separately;
   season alone never triggers a hard reject.
6. TikTokShop manual `(v)/(x)/(AI)` learning is isolated by
   `apparel:<family>:<season>` when a specific season exists, to avoid mixing
   feedback from Christmas and Halloween. Existing per-campaign feedback with
   prior `apparel:<family>` labels is still read only for the same campaign.

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
