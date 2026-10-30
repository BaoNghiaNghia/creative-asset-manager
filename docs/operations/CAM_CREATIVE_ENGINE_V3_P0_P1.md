# CAM Creative Engine V3 — P0 baseline and P1 read-only bridge (2026-10-30)

**Scope:** GatorHats Products Stage 0 → Stage 1 → Stage 2 on Valucap 8869, exact quote text, approved Typography Style DNA. V3 is **not** a substitute for Stage 1 generation or Stage 2 ThreadTransfer.

## P0 — verified main source boundaries

- **Stage 0:** `RrugcKeywordVolumeModel` owns the exact user keyword and picked state; never auto-normalize its original characters.
- **Creative Brief V1:** `creative_brief.py` uses a single bounded Codex text-only request and checks the original exact keyword and three distinct concepts. `creative_brief_service.py` persists its own row in `rrugc_keyword_creative_briefs`, runs only for picked keywords and uses the existing queue. `creative_brief_router.py` restricts reads and runs by tenant/permission.
- **Typography V2:** `typography_router.py` + `typography_preview.py` provide explicit tenant-canary, rights-approved reference and Style DNA controlled preview. `typography_generation_plan.py` drafts a six-slot legacy generation plan, not verified output. `TypographyStyleLab.tsx` already shows structural SVG previews; its Stage 2 handoff is independently gated.
- **Stage 1 existing output:** `keyword_images.py`, `typography_imagegen_pilot.py`, `typography_output_history.py` and the V1 six-slot contract are protected. Do not fabricate output slots or delete existing revisions.
- **Stage 2:** actual official stock and front/3/4 ROI are managed by separate existing modules. P1 does not modify cap pixels, crown/visor palettes, or thread transfer.

## P1 additive output now available (code only; activation must be explicit)

- `creative_engine_v3.py`: deterministic, versioned `creative-v3.0-preview-v1` contract. Builds up to three stable concept IDs from an **already-completed** V1 brief; preserving `keyword_exact`, a content hash, a keyword hash and the V1 prompt version.
- No second Codex call. Any promise of a separate trained V3 planner is **not** made: `planner_status=v1_bridge_preview_only`.
- Meaning-first idea, audience, familiarity and twist are **V1 descriptions**, not mechanically validated design layers. Creative Twist/Explore mechanisms remain `null` and `needs_review` until a structured V3 planner/composer exists. Safe Typography's `NO_TWIST` is only a suggested grammar, not a validation.
- No ImageGen, no approval, no output history writes, no migration, no worker or Stage 2 action; `can_generate_artwork=false`, `job_created=false`. A V1 brief that is missing, running, failed or stale does not generate fake concepts.
- New read-only routes at `/api/v1/realistic-review-ugc/keyword-creative-briefs`:
  - `GET /v3/status`: permission-bound capability probe, returns `available:false` by default.
  - `GET /{keyword_id}/v3-preview`: only for explicitly opted-in tenant and a still-picked keyword, with no cross-tenant leakage.
- `TypographyStyleLab` shows an optional non-generating V3 concept-selection section with exact-text lock, hook and QA reasons. The existing structural Style DNA previews and V2 handoff controls are independent and unchanged.
- **Feature gate:** both `CREATIVE_ENGINE_V3_ENABLED=true` **and** inclusion in `CREATIVE_ENGINE_V3_CANARY_TENANT_IDS` are necessary. Both defaults OFF. This flag never launches jobs, grants access, or bypasses Typography V2 rights.

## Evidence and limitations

- New backend contract tests cover byte-exact 17 **synthetic** representative phrases, three modes, stable fingerprints, changed sources, SVG limits, separate tenant identity and default-off gate. These are **not** the 17 user-provided real keywords in the V3 architecture acceptance criterion.
- Real-world semantic validation against those 17 picked user keywords, contrast/geometry QA, real transparent artwork output, and physical embroidery have **not** occurred. Do not label them PASS.
- The V3 contract exposes no protected reference content or unverified rights. A real Style DNA ID is only associated after separate server-side approval and consent, not by joining client-provided fields.
- Do not enable P2/P3/P4/P5 via the P1 flag. The user must explicitly approve chosen concept/artwork before production generation.

## Next logical phases

1. **P1.2 real V3 planner**: versioned structured semantic grammar/mechanism and minimal cost QA, persist under a separate V3 version without overwriting V1. Only launch explicitly selected, bounded model calls using current queue.
2. **P2**: approved concept → standalone RGBA thread-aware Stage 1 artifact, exact-text and alpha QA, stable provenance, Output History.
3. **P3**: visor/crown color-context contrast check and explicit palette variants on the same immutable master; no physical visor embroidery.
4. **P4**: ThreadTransfer ROI edit on original official front and 3/4 stock photos with preservation gates.
5. **P5**: explicit pick/reject/revision feedback and dedupe fingerprints, not online model training.

**Rollback:** deactivate the two V3 flags independently of V1 or V2, then use the retained standard release. Source and historical Stage 1 records are not mutated by P1.
## Verification snapshot (2026-10-30)

- Backend targeted tests: 46 PASS. Frontend Style Lab / Stage 1 / Creative Brief tests: 15 PASS. TypeScript typecheck: PASS.
- Focused Playwright fixture on local Vite: desktop 1440x900, tablet portrait 768x1024 and mobile 390x844. Each captured Stage 1, Style Lab, V3 three-brief preview and selected Explore state; **all functional assertions passed**, zero page/console errors, zero unexpected HTTP responses, zero horizontal overflow, zero undersized or unnamed controls.
- **Strict runner warning remains:** 9 `net::ERR_ABORTED` GETs per viewport triggered by existing Stage 1 fixture navigation/component polling. This is counted as a failure by generic strict QA. No changes to the runner's request-failure classification, no baseline refresh and no claim that strict gate passed.
- V3 production activation is a separate step: both `CREATIVE_ENGINE_V3_ENABLED` and explicit tenant canary must be set; do not activate automatically merely because the code has been deployed.
