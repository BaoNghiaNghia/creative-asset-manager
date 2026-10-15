# Branding DNA — Pre-implementation optimization and decisions

**Review date:** 2026-10-15. This checklist is the implementation baseline, not a claim that any Scanner is operational.

## Critical corrections from initial design

| Area | Risk in first design | Decision / enhancement | Gate |
|---|---|---|---|
| Product coverage | `scan all products` overclaims marketplace access | **Accessible coverage** = successfully analyzed eligible listing identities / independently observed accessible listing identities; record `scope_known/partial/unknown`, undiscovered pages and blocked listings | P0 |
| Discovery vs gallery | Titles alone become “verified” shop style | Evidence must refer to **actual listing image** + source + hashes/order/variants; claim status `observed/inferred/unknown` | P0 |
| Shop identity | Similar display names on Amazon/Etsy or regional storefronts collapse | canonical `tenant + platform + seller/shop ID + marketplace/region`; alias table for URLs/names, redirect history | P0 |
| Multiple variants | Same shirt/hat counted many times; gallery order changes | separate product, variant, listing revision, gallery image, exact and perceptual hash; sample at product and variant levels | P0 |
| Scope/legal | Public access assumed equivalent to API permission | Shopify Admin/Etsy API/Amazon SP-API for authorized access; public browsing only where permitted; anti-automation gates pause, not bypass | P0 |
| Shop quality | Strong style conclusions from 5 curated hero images | Balanced stratified samples across category, date, gallery role and variants; show denominator and confidence/uncertainty; human review | P0 |
| AI inference | Hallucinated brand persona/target audience | Clearly distinguish observable photographic patterns from marketing hypotheses; include evidence and counterexamples | P0 |
| Scan queue | One long crawl blocks entire worker; resumes repeat scans | Per-listing task, immutable manifest, worker lease, checkpoints, idempotency, requeue with lease fencing, fairness | P0 |
| Browser isolation | Reusing Pinterest Chrome profiles/cookies | Separate Agent + pinned Browser runtime + profile lock per shop/tenant; outbound-only authenticated CAM control | P0 |
| Scanner observability | Heartbeat shown as scan success | Separate live, productive, blocked, stale; counters, queue lag, last committed item and retry reason | P0 |
| Security | User input allows localhost / redirect SSRF | Server allowlist, DNS/IP resolution per hop, content byte limits, signed evidence upload URL, scopes and log scrubbing | P0 |
| Costs | Gallery-wide Gemini calls for unchanged products | Store source/content/image hashes, delta scanning, role-aware sampling, model budget/queue cap per shop | P1 |
| Profile lifecycle | New scan silently overwrites approved DNA | Immutable approved versions; draft changes, diff, approval and rollback; Apply to Workflow references pinned version | P0 |
| Changing platform layout | Source adapter breaks silently | Adapter contract fixtures by marketplace/locale, canary scan, automated parser drift detection and pause | P1 |
| Deletions | Product disappearance treated as missing evidence/erased knowledge | Tombstone last-seen and explicit inaccessible state; snapshot provenance preserved within retention policy | P1 |
| Data privacy | Screenshots capture customer identifiers/PII | Minimize raw screenshots, crop product gallery, redact reviews/customer details, consent and copyright retention review | P0 |
| Cost/exposure | Public shop images hotlinked or cached forever | Explicit licensing/retention controls, private thumbnails, evidence quota by tenant and deletion/export flow | P1 |
| UX | Mock CTA confused with operational scan | Preview badge until APIs healthy, Start disabled until authorization/worker/coverage checks, distinguish estimate vs confirmed | P0 |

## Proposed domain model (consistent `bc_*` prefix)

`bc_shops, bc_shop_aliases, bc_access_grants, bc_source_adapters, bc_browser_agents, bc_scan_runs, bc_scan_tasks, bc_listing_keys, bc_listing_snapshots, bc_listing_variants, bc_listing_images, bc_media_manifests, bc_ai_analyses, bc_evidence_claims, bc_profile_versions, bc_profile_bindings, bc_profile_feedback, bc_scan_events, bc_agent_heartbeats`.

Required keys/constraints:
- all data tenant-scoped; unique canonical shop per `tenant_id + platform + canonical_shop_id + marketplace`;
- listing ID/variant are independent of `display_title`; snapshots carry `source_revision`, `captured_at`, `last_seen_at`, `content_hash`, provenance and explicit access quality;
- every image has original sequence index, `role`, source URL hash, consent/licensing category, R2 object key *if allowed* and references;
- profile claim has `claim_kind`, `supporting/contradicting_evidence_ids`, sample count, confidence, model/prompt version;
- append-only audit of scan claims and approvals, optimistic-lock approval transition, idempotent worker outbox inserts;
- deletion/tombstone compliance and R2 retention/TTL policy tracked by owner, not guessed.

## Brand DNA analysis contract

Six layers retained: Shop, Category, Listing Visual, Image Style, Platform and Campaign. Structured output must pass JSON schema validation. Pipeline:

1. Compute exact SHA256 and optional perceptual hash, deduplicate repeated listing gallery images.
2. Classify image roles (Hero, Detail, Lifestyle, UGC, Variation, Info) with abstention on low-confidence images.
3. Extract strictly pixel-grounded facts and explicit text evidence; do not treat seller slogans as a fact about audience psychology.
4. Stratified aggregate with denominators, counterexamples, bias indicators and `unknown` when evidence is weak.
5. Generate immutable **draft** profile version and a structured diff; human approves or rejects individual claims and entire version.
6. Apply only approved version to the selected shop, output role/category and workflow. Track output evaluation separately from profile learning.

**Quality bar (pilot target, not measured fact):** ≥95% extracted listing identity accuracy on eligible fixtures; ≥95% exact gallery order on known items; 100% evidence lineage from any **approved** claim to at least one authorized source observation; 0 cross-tenant leaks and 0 automatic approvals. Establish extraction recall and false-positive baselines during pilot before setting per-marketplace production thresholds.

## UI / interaction corrections

- Add Shop wizard includes **Owned / Authorized / Public observation** source mode, store URL verification and per-platform capability matrix; no automatic Start Scan at Create.
- Scan status distinguishes `Discovered`, `Eligible`, `Attempted`, `Analyzed`, `Blocked`, `Missing`; show unknown denominator if no authoritative feed.
- Product Explorer has tree, gallery role inspector, provenance, missing/image rights flags, filter “Changed since last scan”, and list pagination on server.
- Brand DNA view adds evidence citations, sample coverage warning, comparison of last approved vs draft, feedback rationale, approval audit.
- Worker Manager modal adds `Agent ID / host / version / heartbeat / browser status / active profile / queue lag / CPU/RAM / disk / pause / update / disable`.
- Keep compact UI and shared responsive pattern from CAM; ensure mobile does not offer misleading inaccessible crawl controls.
- Keep all actions typed: disable `Start` until backend reports `ready_for_scan`, display blockers with retry/help. No hidden automatic scan.

## Performance and operability

- Scan: manifest-based incremental discovery; use ETags/last-modified when available, else stable normalized page fingerprint, avoid noisy prices/view counters.
- Separate API fetching (where authorized), browser DOM, image processing, Gemini and aggregation stages; each stage own budget, semaphore and retry classification.
- Worker uses persistent queue with lease and local SQLite outbox; distinct process from Pinterest Scout; per-shop exclusive profile lock.
- Use Gemini pool fairness, quota reservation and backpressure; no automatic unlimited retries during outages.
- Bounded storage for listing images and browser traces; cleaning based on ownership/retention and user deletion request, not global blind disk purge.
- Canary adapters with HTML/API fixtures for US locale first; no “all regions supported” claim before evidence.

## Ordered work before real browser launch

**P0:** canonical route/nav/terminology; domain schema/RBAC/API; agent control and safe URL intake; per-shop checkpoints; platform eligibility; evidence lineage; manual approval; health and stoppability.

**P1:** signature/media dedup, structured Gemini extraction, delta refresh, retention/export, platform fixture coverage, worker telemetry and responsive QA.

**P2:** automatic refresh cadence, multi-worker scaling, similarity clustering, trend detection and cross-shop comparative analytics only on explicitly permitted datasets.

See [dedicated worker decision](./WORKER_ARCHITECTURE.md) and [readiness gates](./READINESS_AUDIT.md). No active automation is provisioned by these documents.
