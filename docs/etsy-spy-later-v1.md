# CAM Etsy Spy — Later & Related Listing V1

Date: 2026-11-07. Scope: CAM Etsy Spy Production, `main`.
No Windows extension change, no external AI API, no download of product media.

## User behavior
- In product Action column, compact clock + **Later** appears immediately right of **Open on Etsy**.
- Card order: Eligible listings, Picked, **Later**, Blacklist, Win Keyword, Keyword nodes.
- Clicking Later on a qualified listing saves it as a **manual topic seed**, instantly scans current active listings, and moves matching products into the Later table. Future Windows syncs classify newly observed listing IDs automatically.
- Clicking a seed's Later again removes the seed and recalculates related matches. Clicking a system-related entry's Later removes only that suggestion and suppresses re-additions after new syncs.
- Pick is a higher-priority manual choice; Picked entries never appear in Later or Eligible. Unpick restores a matching Later entry.
- Blacklist, Custom-title exclusion, valid badge and views/carts eligibility continue to apply to the Later display. Scout source, discovery tree, Windows source DB, and trademark pipeline stay unchanged.

## Matching
- Input: observed Etsy **title** (not parent discovery keyword, because a broad search seed would create false matches).
- Normalize ASCII accents/case/punctuation. Remove apparel type, brand, material, color, size, SEO/marketing terms.
- Accept an explicit, high-signal design-topic equivalence (e.g., `plant/houseplant/botanical/gardening/succulent`, `goose/geese`, `frog/toad`, `Rudolph/reindeer`, `book/reading`, `overthinking/critical thinking`).
- Otherwise require **at least two** informative overlapping terms plus coverage threshold. Weak single tokens (e.g. funny, shirt, Christmas, gift, Comfort Colors, Gildan) never trigger grouping by themselves.
- Each match saves score, reason and source seed ID; the UI displays the explanation. There is no probabilistic model, embedding, training job or AI API in V1. **Semantic coverage is limited to the explicit concept groups**, not arbitrary natural-language paraphrases; extend vocabulary after reviewing incorrect suggestions.
- Single manual seed with only generic marketing words remains manual Later, but cannot auto-group.
- Automatic queue does not change manual Pick or blacklist; no auto deletion of source listings.

## Persistence
Alembic revision `0202_etsy_spy_later` adds:
- `etsy_spy_later_seeds`: (tenant_id, listing_key) PK, source title, explainable topic-analysis JSON and created_at.
- `etsy_spy_later_matches`: (tenant_id, listing_key) PK, seed key, score, reason, state (`matched` or `dismissed`), updated_at.
- Composite tenant/state index. A dismissed row is retained for future sync suppression.

## API
- `PUT /api/v1/etsy-spy/listings/{listing_key}/later` JSON `{"later": true|false}`, manage permission, tenant ID/qualification check, idempotent.
- `GET /api/v1/etsy-spy/listings?pick_state=later` fully paginated and **deduplicated by Etsy listing ID** through SQL row-number ranking.
- `GET /api/v1/etsy-spy/summary` returns distinct `later` and number of `later_seeds`.
- Existing `GET /listings?pick_state=eligible` and `picked` keep existing sort/page behavior; eligible excludes Later, picked takes precedence.

## Sync and operations
- `sync_batch` does an atomic cheap incremental match only for changed listing IDs when seeds exist. No new browser tabs, no Windows AI calls.
- Adding/removing a manual seed backfills the existing active tenant titles and re-evaluates the queue once.
- No migration of Picked, no changes to Windows sync schema, no auto-download.
- Before production: run Etsy Spy backend and frontend tests, frontend typecheck and build, Alembic revision check and deploy with migration enabled. Retain current/previous release.
- Rollback: older app code ignores the two new Later tables. Avoid automatically downgrading/erasing their contents during application rollback.

## Future improvements
Optional local text-embedding reranking behind a feature flag, after evaluation dataset/false-positive review. Never silently claim that these explicit synonym groups understand all semantic paraphrases.
