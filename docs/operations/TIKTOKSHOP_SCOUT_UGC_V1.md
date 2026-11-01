# CAM TikTokShop — Scout & UGC (Stage 3–5) implementation runbook

Updated: 2026-11-01
Status: Phase 1 (isolated Drive source discovery and navigation); Scout/generation activation gated.
Branch policy: production deployments from main only; do not copy/migrate GatorHats output records.

## Product contract

New top-level sidebar `Tiktokshop` → `Scout & UGC` at
`/tiktokshop/scout-ugc`. Three page tabs reuse the **same GatorHats Reviews
Stage 3/4/5 visual language**:

- Stage 3 — Pinterest References: Drive source → AI product-context analysis → Pinterest → feedback & dedupe.
- Stage 4 — Image Generation: choose suitable references, product-aware edit Skill, retries and output version history.
- Stage 5 — UGC Review: group by product folder, analyze finished outputs, inspect before/after, review and export only authorized outputs.

Do not duplicate the entire engine or create a second worker queue.
Keep GatorHats logic unchanged. Preserve all existing images, versions,
campaigns, feedback, reviewed outputs, and pending/running jobs.

## Two immutable source namespaces

| Workspace | ID | Google Drive root |
| -- | -- | -- |
| GatorHats Reviews | `gatorhats` | `1kNBQU4O-i6cbDBnRrhPGNENHvieWYPfX` |
| TikTokShop | `tiktokshop` | `1LS8_v9HL-OmucwRFLcFwRU7iW5r3xJkh` |

The TikTokShop folder contains various embroidered apparel/sweatshirt products.
Do not force hat shape, Valucap 8869, trucker cap, visor, or Gator branding into
TikTokShop AI prompts or scoring.

**Drive folder permissions warning:** On 2026-11-01 folder metadata reported
`type:anyone, role:writer`. Restrict sharing after checking active external collaborators;
do not mutate ACLs as part of this feature rollout.

## Isolation invariants (must hold server-side)

1. The authenticated tenant and **trusted server-side workspace root** must be
   in every Stage 3/4/5 list, overview, sync, queue, analyze, delete/hide,
   regenerate/retry, preview, export, and output-history query. Never rely on
   a UI-only filter or user-supplied arbitrary `root_folder_id`.
2. Source-plan uniqueness includes `root_folder_id`. Existing campaign grouping
   is root-scoped on initial sync, but **agent claims are tenant-wide today**;
   therefore TikTokShop analysis/auto-scout may not be enabled without agent scoping.
3. Campaign, candidate, feedback, reference, scout run, stage2 job, stage3
   analysis, output version, and export inherit the validated workspace root
   from the associated source plan. When a request includes an object ID,
   resolve its parent plan root and return 404/403 on cross-workspace access.
4. Source file, output folder and thumbnail IDs must not be accepted as
   arbitrary cross-root write destinations. Store output in the original
   selected product subfolder or a scoped `UGC-Outputs` child, never Gator root.
5. Results from different roots must never share scout campaign IDs, learning
   feedback training, pin-scanned memory, run slots or stage5 export sets.
   Same external pin may exist independently in both workspace namespaces.
6. Gator current endpoints and bookmarks default to Gator. TikTokShop endpoints
   require explicit workspace selection. Unknown workspace values fail closed.
7. Route access uses RRUGC permission temporarily; introduce an independent
   TikTokShop navigation/role permission before separate team rollout.
8. Lifecycle: no deletion or rewriting existing Drive files on scan/reconcile.
   No destructive migration/backfill. Keep output history and rollback release.

## Current backend audit and blockers

- `source_plans.py`: `sync_source_plans(root_folder_id=...)` can already ingest
  separate roots, but current Gator workflow implicitly turns on `auto_scout`
  as soon as analyzed campaigns are created.
- `scout_automation.py`: `RrugcAutoScoutService.claim` currently picks
  `repository.claimable_campaigns(tenant_id,...)` without agent workspace.
  Creating a second agent token today does **not** isolate claims.
- `analysis.py`: `ANALYZER_VERSION=rrugc-reference-v11-hat-review-scenes`
  and `product_fit_score` favors front crowns and hat/head metrics; TikTokShop
  requires apparel/product-aware masks, natural wearer context and a different QA policy.
- `stage2.py`: `DEFAULT_STAGE2_SKILL=gatorhats-8869-image-studio`,
  `DEFAULT_STAGE2_PROMPT=8869 Five-Panel Twill Cap`; neither is suitable
  for TikTokShop. Add `tiktokshop-apparel-ugc-v1` with product preservation
  and anti-hallucination QA, and enforce allowed Skills by workspace on backend.
- `router.py`: Stage4 active/completions/queue control and Stage5 list/analyze/
  export currently query ALL tenant rows. Their TikTokShop paths must be fully
  root-scoped **before** enabling Stage4 and Stage5 data.
- Stage5 Loox export contains synthetic review identity/date templates; do not
  label invented testimony as real verified purchase reviews. TikTokShop outputs
  should support draft creative concepts and genuine-author-supplied review data.

## Phased implementation

### Phase 1 — safe shell and independent source metadata (implemented)

- Top-level TikTokshop menu and Scout & UGC submenu, route and 3 tabs.
- Stage 3 reuses the source plan table, its filters/tree/pagination; UI shows the
  TikTokShop root ID.
- `/source-plans`, `/source-plans/folders`, `/source-plans/sync` support
  trusted `workspace=tiktokshop` with explicit server allowlist.
- TikTokShop Scan is **metadata-only** (`queue_analysis=False`): no new
  claimable campaign, no ImageGen, no Stage4 jobs, no Gator references.
- Stage 4/5 remain visibly gated, showing no Gator tenant-wide jobs or reviews.
- No migration and no Windows agent update in this phase.

### Phase 2 — per-workspace Auto Scout and Windows Agent

Implement one durable namespace field on `rrugc_scout_agents` and campaign/
source-plan membership. Migrate existing agents to `gatorhats`, default
unknown new agents to no claimable scope. Enforce `agent.workspace == source_plan.root`
inside a single transactional claim/lease operation. Validate same membership
during candidate batch submission/heartbeat/finish and prevent claim reassignment.

Windows host: `DESKTOP-3FR0LF0`, checkout `D:\Desify_Agentic\scan_pinterest`.
Reuse `apps/rrugc_scout/scout.py` and the current Manager/PowerShell launcher,
not a second codebase or API-key-paid service. Add **TiktokShop Scout** as a
separate Manager worker/card with:

- a separately paired `TIKTOKSHOP_AGENT_ID` and token stored only in an
  ignored local env/Windows-protected credentials;
- Chrome profile `pinterest-profile-tiktokshop`, never shared/nested with
  Review, Keyword, Cap Stack or Typography profiles;
- independent instance lock, PID, logs, local SQLite outbound event spool,
  durable scanned pin history, auto-login state, interval, pause/restart,
  bounded crash recovery, backoff and quota resume;
- username/session already present -> skip sign-in; manual login only on
  Pinterest challenge, never attempt an automated security bypass;
- **workspace-aware** terms/search: garment, sweater, sweatshirt, tee, POD
  embroidery, lifestyle UGC, plus user-supplied product context and reference
  photo; no `Saying Trucker hat` seed or hat-specific metadata rejects;
- claim/submit/heartbeat/complete protocol contains authenticated workspace
  identity and product source-plan ID, idempotent pin/source fingerprint,
  reviewed/reference feedback; no ever-shared history file or profile;
- one worker loop, one reusable detail tab, low concurrency and no new image
  calls except existing CAM provider policy. Crawl public Pinterest pages
  compliant with site rate limits. No scraping private TikTokShop account data.

### Phase 3 — product-aware generation and Stage 5

- Product context classifier: garment category/material, front/back graphics,
  scale, physical seams/folds, lighting and wearer/body geometry.
- Use one product-aware Image-Edit Skill; accept exact source product,
  1–3 selected Pinterest scenes, a mask if available. Edit without inventing
  text, replacing logo/color, or distorting the garment silhouette. Fail QA
  closed on wrong product, washed-out embroidery, AI hand issues, floating
  decals, or missing texture; do not copy Gator hat prompt or randomize colors.
- Stage 4 read/launch/versions/retry/regenerate/queue must all honor workspace.
  One shared Codex pool is fine with per-workspace fairness and budgets.
- Stage 5 analyzed UGC frames must use apparel-specific policy and only
  TikTokShop output jobs, not Gator. Default review export is **draft-only**.
- Build/read-only QA: cross-workspace negative tests for every endpoint,
  scanned folders, campaign claims, source plans, reference feedback,
  stage4 idempotency, output storage, stage5 listing/export and review hiding.
- Run Windows Scout pilot on 1–2 existing TikTokShop product folders and
  inspect picked references (v/x/AI). Run a single Stage4 sample and Stage5
  export preview before enabling automatic broad discovery.
- Deploy main with tests and smoke, verify API/image worker/Scout heartbeat,
  activate with a feature flag, monitor quota and disk; preserve Gator jobs.

## Acceptance checklist

- [ ] The top-level menu opens at `/tiktokshop/scout-ugc` on desktop/mobile.
- [ ] Stage3 TikTok source count/folders match Drive, Gator results cannot appear.
- [ ] Gator Stage3/4/5 output and routing unchanged.
- [ ] TikTok agent **cannot claim** a Gator campaign; Gator agent **cannot claim** TikTok.
- [ ] TikTok/Pinterest browser and state isolated; manager restart is safe.
- [ ] Reference quality approved/rejected feedback affects only the matching root.
- [ ] Stage4 refuses any Gator-specific Skill in TikTokShop, accepts correct product.
- [ ] Stage5 no foreign root results; export clearly labeled concepts/drafts.
- [ ] Same-scoped job retries dedupe; no lost history across restart.
- [ ] No unauthorized public Drive sharing or accidental cross-root writes.
- [ ] Frontend and backend tests, API live/ready, image workers, production smoke pass.

## Operational continuation

The standing project policy uses `main` for code and production release.
Phase 1 is intentionally non-generating. Do not switch on Scout or Stage4/5
automations until Phase 2/3 acceptance gates pass. Continue with claim namespace,
Windows worker and product-aware QA rather than cloning a second backend.
