# CAM Keyword Intelligence V2 Advanced — Delivery and safety contract

Date: 2026-11-03. GatorHats Products Stage 0 and Stage 1, `main`.
This release uses the existing Creative Brief and G3 tables. No schema migration,
provider billing, auto-scanning, auto-approval or unbounded retries.

## Phase 3 — Cross-keyword learning
- Cohort is inferred from a conservative word-token allowlist (music, western,
  pets, healthcare, coffee, family, outdoors), not identity or protected traits.
- Query only current human Pick/Reject decisions for this tenant, latest 800
  reviewed Stage1 outputs within 180 days.
- Exclude the target keyword. Minimum 3 distinct other keyword phrases and 6
  reviewed outputs in the same cohort; each advice pattern must have at least 3
  phrases and 3 negative votes. No arbitrary recommendations when insufficient.
- Only fixed reason enum and server-authored guidance enter ImageGen/Creative
  Brief prompts. Other phrases and images never leave the DB for this feature.
- Individual decisions remain reversible; one reject never blacklists a cohort.
- Exact keyword feedback still has precedence. Keep 1/4/4 design diversity.

## Phase 4 — Visual + Market evidence
- G3 read-only evidence: keyword-scoped, tenant-owned/authorized and editorial
  approved references, current matching source hash and confidence >= 0.65.
  Show style family, font mood, compact layout graph features and similarity pairs
  (dHash Hamming <=6); stale manual ROI hashes are not considered.
- G3 is opt-in and does not run provider analysis on reading Stage 0.
- New Stage1 2048x1024 transparent artwork gets an alpha-bounded typography
  dHash. Reject on repeat of a *human-rejected image for the exact keyword*
  at strict Hamming <=2, with at most the existing one technical QA retry.
  This is a heuristic to avoid close repeats, not a letter-by-letter OCR
  verification, nor proof of rights/originality. No old archive image backfill.
- Market evidence shows stored Google Ads volume, competition, 3-month trend,
  YoY and checked date. Trademark validation is separate and source-bound.
  Never infer product sales from search ads. Show missing evidence honestly.

## Stage 0 global ranking
- Compact Rank: Opportunity toggle and server-side filter
  All / Prioritize / Explore / Hold / Needs analysis.
- Ranking/filtering uses SQL LEFT JOIN to tenant's latest Creative Brief,
  calculated BEFORE `LIMIT/OFFSET` and count. No client-only page sort.
- Scores use configured weights Buyer25 Creative25 CapFit20 Market20; G3
  Originality10 remains null unless a later verified output analysis exists.
  Missing coverage <60 leaves score null (Needs analysis). Null scores remain
  last; phrase length is only a deterministic preflight ordering fallback.
- Human reviewed trademark risk is NOT legal clearance; source screening
  cannot raise priority if it was done on an outdated/re-written phrase.
- Existing Stage 0 columns, zero-volume filters, pagination, click-to-Pick
  and Stage 2 product placement are unchanged.

## Current production evidence at development time
- 8,789 keyword records; 813 Creative Brief records;
  0 analyzed G3 DNA rows; only 1 Stage1 feedback record.
- Consequently, cross-keyword advice and G3 comparison will typically show
  a precise "insufficient evidence" status until new human ratings and
  approved analyses become available.
- An initial read-only PostgreSQL query showed 8,673 keywords currently
  classified Needs analysis. This is a diagnostic observation, not a sales claim.

## Operational checks
- Unit: keyword cohorts, rights scope, visual hash, SQL/Python score parity.
- API: tenant separation, global sorting before pagination, original routes.
- Frontend: TypeScript and Stage0/Keyword Insight component tests.
- No destructive data migration; keep rollback and release cleanup constraints.
