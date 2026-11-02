# TikTokShop Scout: apparel placement-aware reference selection

Date: 2026-11-02. Scope: TikTokShop Scout & UGC, Stage 3 (Pinterest) through Stage 4.
GatorHats Reviews / hat Scout remains unchanged.

## Problem and contract

Generic candid relevance/UGC scoring previously let through back views, tiny shirts,
hands across chest, sleeves out of frame, hair/bags/phones over lettering. Artwork
on collar, chest, sleeve, upper arm, and combinations has different required photo
geometry; a high-quality lifestyle photo is not necessarily replaceable.

Pipeline:

1. Existing source-photo Gemini contextual analysis extracts product type and
   **observed** apparel design placements, wearer-relative left/right, priority
   (one primary, remaining secondary), confidence; no invented unseen graphics.
   Store in existing `visual_context_json.apparel_placements`; no DB migration.
2. Existing source context builds Pinterest photo queries that preferentially find
   clear garment front, neckline, or three-quarter arm/sleeve, while retaining
   contextual scene search. The Windows TikTokShop Scout uses existing query
   payloads without code changes or extra Pinterest API.
3. Existing per-candidate Gemini analysis measures for every required zone
   `visible_ratio`, `occlusion_ratio`, `confidence`, `garment_view`,
   and `garment_frame_ratio`, not just general UGC attractiveness.
   Occlusion includes hair, crossed arms, hands, phone, bags, coat, child/pet,
   tight folding and crop. Left/right are the **wearer's** sides, including mirrors.
4. Deterministic backend gate: hard reject back view, garment under 16% of frame,
   clearly unusable PRIMARY design (visibility under 0.55 or occlusion over 0.30).
   Secondary zone failure, missing/uncertain zone evidence, missing placements,
   unknown view or frame ratio go to Needs Review. Full pass must show each
   required placement; use 0.62 minimum weighted fit score for automatic approval.
   Standard authenticity/AI/duplicate/context gates remain mandatory.
5. Stage 3 displays visible design-zone chips in AI Context and saved per-reference
   fit/rejection explanation in thumbnail hover details. The status remains derived
   from the actual candidate review state and never silently auto-approves.
6. Stage 4 refuses new TikTokShop outputs from any reference that has not passed
   the zone gate, unless the operator explicitly marked it suitable. The apparel
   generation prompt preserves the garment design zones and avoids occluding props.

## Preservation, cost, rollout

- Two existing vision calls (source and candidate) produce the new data. No extra
  ImageGen calls, no new dependency, no database schema/index, no Windows
  client update, no browser profile or agent re-pairing.
- TikTokShop source context upgrades gradually by existing source-plan scheduler,
  reusing the 12-job tenant/workspace in-flight Gemini budget. Source images,
  campaigns, all saved candidates, old ratings/feedback, and generated UGC
  outputs remain intact; no destructive bulk replay. Historical candidates
  without new visibility evidence are not auto-eligible for Stage 4 until
  evaluated or explicitly manually approved.
- Campaign artwork grouping includes apparel zone names only in TikTokShop, so
  a design on a left sleeve cannot share a group with the same lettering
  at center chest. GatorHats signatures and query patterns remain unchanged.
- Manual Good can override uncertain/blocked references intentionally; an
  AI-generated reference already marked Bad/AI remains blocked. Scope stays
  tenant and source-root isolated.
- Provider output is probabilistic: source location/pose estimates should be
  checked against actual photographs on a small first run. A test pass alone
  does not prove no design can ever be obscured.

## QA / rollback

Backends: test_apparel_placement, test_product_context, test_source_plans,
test_source_plan_scheduler, test_rrugc tiktokshop, Stage 2 reference contracts.
Frontend: TiktokShopScoutUgcPage, SourcePlanTable, TypeScript. Production
verification: backend live/health/ready, serving frontend build-info and public
route smoke. Undo via previous immutable API/frontend releases; preserve
source-plan and campaign data. No migrations to roll back.
