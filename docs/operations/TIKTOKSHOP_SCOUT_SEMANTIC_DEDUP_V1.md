# TikTokShop Stage 3 — Semantic Scout and workspace de-duplication V1

Updated: 2026-11-05. Scope: TikTokShop Scout & UGC, Stage 3 only.

## Root cause (verified in code)
- Windows v58 consumes only the first four `source_context.search_clusters.direct` queries. CAM previously replaced all source-specific keywords with generic apparel placement searches such as `person wearing sweatshirt`, yielding overlapping Pinterest candidates across unrelated products.
- The claim's `known_pin_urls` previously included only the current TikTokShop campaign. Candidate ingestion and perceptual-fingerprint analysis also treated different TikTokShop products as separate dedupe pools.
- The reference feedback response endpoint read the GatorHats learner by default even when explicitly serving TikTokShop; the core candidate analysis handler already used a workspace-scoped learner.
- The persisted learning intent used generic `product:sweatshirt`, blending Grandma and newlywed reference preferences.

## Implemented behavior
1. Reuse the existing visual apparel placement confidence gates. Read the original product filename plus saved visual embroidery text/identity; derive a deterministic audience/occasion (Grandma, Godmother, mother/daughter, engaged, newlywed, etc.). Each claimed query contains garment, subject/occasion and chest/sleeve/collar requirements. No new AI call, embedding service, database table or paid API.
2. Keep four query lanes in the claim, vary three across attempts and copy them in identical order to `source_context.search_clusters.direct` for the existing Windows v58 runner.
3. Read human `(v)/(x)/(AI)` reference feedback using the existing per-tenant, per-workspace ledger and new `apparel:<family>` learning intent. Only two or more consistent human votes can reorder queries; unreviewed imports do not vote. Manual `(AI)` remains a negative signal.
4. Provide known-Pin hints across TikTokShop campaigns, never across GatorHats. Server ingestion blocks exact Pin re-use from another TikTokShop product when the earlier candidate is in an active/pending/approved status, including pre-analysis. A human-rejected Pin remains eligible for another product; it is not considered a positive deduplication anchor.
5. Check perceptual fingerprints before Gemini across approved, non-rejected TikTokShop references, rather than only the same TikTokShop campaign. The GatorHats path is separately root-scoped. This handles visually similar reposts using the existing no-cost VPS fingerprint check. Explicit human acceptance still overrides automatic rejection according to existing review policy.

## Deployment and operational limits
- No schema migration, new credentials or Windows client reinstall required. Backend-only release via the reviewed main release mechanism.
- Already stored candidates and Drive assets are **not deleted or rewritten** by this feature. Historical duplicate previews may persist until a separately reviewed reconciliation or new scout cycle. Run a dry-run census before any old-data cleanup.
- Semantic intent is a deterministic heuristic. It will not always infer an unfamiliar design correctly; generic fallback is retained when source evidence is insufficient. Deep image-text matching and cross-Pin photo identity beyond currently approved perceptual fingerprints remain possible future improvements.
- Tenant and workspace isolation is enforced in all new reference lookups. Unknown workspaces remain rejected.
- Rollback: return to the previous verified release. Candidate data already written under the new dedupe policy remains auditable; no data migration rollback.
- Existing visual baseline/UX changes are out of scope; reuse the current UI without new controls.
