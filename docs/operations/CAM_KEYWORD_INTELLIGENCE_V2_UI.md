# CAM Keyword Intelligence V2 — UX and delivery plan

Date: 2026-11-03
Target: GatorHats Products, Stage 0 and Stage 1 Creative Brief.
Source principles: Redesign Nurse, Redesign Pet V2, Embroidery Redesign Ideas.
Status: MVP Stage 0 detail drawer and read-only API implemented; later phases pending.

## UX strategy
The existing Stage 0 table already has 14 columns. Preserve its existing
metrics, filters, row density, Scout feedback, and Pick/Unpick control. Add one
compact **IQ** icon in the existing Action cell. Never insert a high-cardinality
score column or an auto-populating full-row AI panel.

Opening IQ:
- Fetches only the selected tenant's exact keyword.
- Opens a right-side drawer with tabs Overview / Buyer / Creative / Learning /
  Evidence, a stable header, scrollable content, accessible close button, and
  fixed action footer.
- Does not send Codex/ImageGen requests, enqueue work or automatically Pick.
- Original Stage 0 details and Google Ads charts remain available under Metrics.
- Responsive: one drawer width, no sideways expanding table, no mobile blocking
  scroll. Empty, loading, error, refresh, queued and stale states are explicit.

## Phase 1: Semantic Intelligence — MVP
- Buyer: display target audience, purchase motivation and meaning/emotion **only**
  from an existing, exact-quote, current-version Creative Brief.
- Empty state is honest; no invented buyer personas or sales evidence.
- Pick & Analyze uses the existing Stage 0 Pick flow; for already picked
  keywords, Analyze uses the existing explicit Creative Brief endpoint.
- No repeat automatic retries.

## Phase 2: Creative Opportunity — MVP
- Creative tab groups 1 Typography-first, 4 Creative Twist and 4 Explore
  variants from the existing brief, without rewriting original quote.
- Text/cap preflight is explicitly estimated from character/word count; actual
  embroidery geometry still needs the Stage 1/Stage 2 QA gate.
- Scorecard weighting: Buyer 25, Creative 25, Cap Fit 20, Market 20,
  Originality 10. Missing dimensions have null scores and do not silently count
  as zero. Full score is suppressed until >=60 weight coverage.
- Score clearly says preliminary and reports coverage and confidence, not
  predicted revenue or confirmed manufacturing feasibility.
- Trademark screening is a separate hold/review gate, never legal clearance.

## Phase 3: Feedback Intelligence — MVP read-only, advanced pending
- Read and show existing tenant/keyword Stage 1 Pick/Reject votes, modes, reasons.
- Never treat rejection of one image as rejection of the keyword.
- Future: audited cross-keyword aggregate traits, minimum sample sizes,
  reversible human decisions, feedback versions and drift controls.

## Phase 4: Market & Visual Evidence — initial evidence view
- Use previously stored Google Ads metrics, source attribution and TM screening.
- Do not claim Etsy/Amazon orders from ad metrics.
- G3 Typography Visual Analyzer remains opt-in and rights-gated; no implicit
  analysis of Scout images.
- Future: rights-aware link of analyzed Typography DNA, visual originality QA,
  evidence age and references; explicit market evidence confidence.

## Follow-on UX improvements
1. Compact Opportunity ranking/filter for Stage 0 only after score calibration
   and server-side pagination/sorting, never client-only sorting one page.
2. Buyer/creative profile handoff shown in the Stage 1 Creative Brief modal
   with version+source provenance.
3. Feedback aggregate view by semantic cohort with thresholds to prevent a
   single Reject from suppressing a broad style family.
4. G3 compare controls for authorized references, including before/after DNA
   distinctions and false-positive override.

## Acceptance
- No Stage 0 existing column, KPI, filter, Scout source or Pick path regresses.
- All API reads require tenant auth; missing/foreign keyword is 404.
- Opening insight creates no job and no provider request.
- Historical Creative Brief versions are marked stale, not recycled as current.
- Zero search volume remains explorable when other evidence exists.
- Originality remains unscored until visual validation exists.
- Responsive and keyboard Escape/focus restoration function correctly.
- Production deployment is optional only after tests and release verification.
