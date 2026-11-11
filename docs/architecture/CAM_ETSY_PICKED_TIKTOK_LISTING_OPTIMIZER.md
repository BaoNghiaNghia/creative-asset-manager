# CAM Etsy Picked -> TikTok Shop US Listing Optimizer (2026-11-11)

## Scope / Source skill

Adapts the user-supplied archive `skill (53).zip` with one
`tiktok-shop-listing-optimizer/SKILL.md` and
`scripts/validate_tiktok_listing.py` into CAM's versioned worker prompt and
strict Python structured-output validator. The archived skill is an instruction
bundle, not an executable service. This integration does **not** install it as
a ChatGPT Skill or call the ChatGPT UI: the existing tenant-scoped Gemini AI
provider analyzes the public Etsy photo and title, then returns draft text.
It is independent of CAM Image Extension's design extraction queue.

Critical limitation: the skill specifies **YTrends MCP ONLY** for TikTok Shop
keyword research; YTrends is not connected to this server. Consequently,
`ytrends_status=unavailable`, `keyword_metrics_unverified`, and
`qa_status=needs_fact_check` are mandatory; generated search terms are
unverified visual suggestions, not researched TikTok search volume, and SMS,
Etsy autosuggest or invented SEO scores must not substitute for YTrends.

## Job lifecycle

- Separate default-OFF `ETSY_TIKTOK_LISTING_ENABLED` flag.
- New eligible Picked item -> same Pick transaction creates one idempotent
  `ProcessingJobModel` of type `etsy_tiktok_listing_generate`, hashed source
  photo/title/pick generation. Independently preserved
  `etsy_design_extract` and SMS operations remain unchanged.
- Existing picked items -> `POST /api/v1/etsy-spy/tiktok-listings/backfill`
  `{"limit":20,"dry_run":true}` previews work without creating jobs.
  An explicit `dry_run:false` enqueues a bounded batch up to 200.
  Repeat requests do not duplicate work.
- CAM image-role worker claims a job via the existing ProcessingJob/tenant
  policy infrastructure, uses the tenant's existing Gemini credential/quotas,
  downloads a bounded HTTPS image from allowlisted Etsy CDN, validates its
  signature/dimensions and asks for strict JSON. The worker rechecks tenant,
  Picked decision, cancellation and exact lease owner before committing output.
- Unpick cancels queued/running jobs; stale result cannot overwrite an unpicked
  listing. Other-tenant access is denied.
- The result is stored in the same authoritative ProcessingJob JSON, with
  `result.title`, `result.description`, `result.highlights`,
  `result.search_terms`, lengths/word metrics, warnings, version and
  draft-only QA status. No new tables or migration.
- The Picked table makes one batched result lookup, shows one compact
  `TikTok Listing` column per product and opens a modal with 4 copyable
  sections. Status changes with standard page refresh; no new background
  browser automation, no direct marketplace publication or AI promotion.

## Structure validation and merchant-claim policy

- Title 25-200 characters; 40-150 ideal.
- Description 30+ words, <=10,000 characters; 500+ editorial target.
- Highlights 3-5; combined <=1,500 characters.
- Search terms <=15; combined strings **including separators** <=250 chars;
  nonempty and distinct.
- Exclude unsupported Made in USA/manufacturing, printing origin, fixed
  shipping windows, tariff/customs guarantee, real blank/brand, size chart,
  artwork rights and other merchant-specific facts from a photo alone.
- Human review required for fit, garment material, accuracy of lettering,
  copyright/trademark, fulfillment and final policy compliance.
- No generated listing is automatically uploaded, rights-cleared or published.

## Security, costs, and rate limiting

Existing Etsy Spy READ/MANAGE permissions and tenant scoping apply. The Gemini
provider can incur quota/cost; keep flag OFF until bounded pilot approved and
model capacity is checked. Do not bulk-enable on hundreds of historical Picks
during initial release. Worker shares tenant policy; AI analysis lane must
not bypass existing operational budget and concurrency limits.

`ETSY_TIKTOK_LISTING_ENABLED=false` is fail-closed and creates no work. The
feature makes no schema change, no new credential, no browser restart and no
modification to Image Extension processing or daily extraction limits.

## Rollback

1. Turn `ETSY_TIKTOK_LISTING_ENABLED=false` in server settings to stop new
   queueing and worker claims; deploy/restart via reviewed backend release.
2. Preserve completed copy drafts and ProcessingJob history for audit.
3. Code rollback only to the distinct prior immutable release; do not
   downgrade 0207, delete jobs or change Image Agent secrets.
4. If a job is already running while a feature is disabled, it must fail
   safely. Do not mislabel or automatically publish existing draft results.

## Delivery gates

Run targeted/new+existing Etsy Spy API tests, Python compilation and Git diff
check. Run frontend typecheck, Vitest, and full CI on pull request before
merge. Production deploy only from merged main with `--no-migrate` and
`--keep-releases 2`, verify all readiness probes and rollback symlink.
The task is not complete merely because code/tests exist: real Gemini
canary, YTrends connector, factual-claim QA and off-host backup restore remain
distinct work.
