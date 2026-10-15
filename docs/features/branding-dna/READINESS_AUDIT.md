# Branding DNA — Actual-run readiness review

Reviewed: 2026-10-15 (source inspection; not a production rollout).

## Current inventory

| Capability | Current CAM | Required before LIVE |
|---|---|---|
| Frontend React, TypeScript, CAM nav and permission gate | Available. Branding DNA prototype route now in source | Replace iframe demo with API-connected screens, add state/error/loading/empty and authenticated Browser QA |
| Original UI design and API contract | Committed in this folder | Final technical API schemas, contract tests, localization and approval of UX |
| PostgreSQL and ORM/migrations | Existing CAM infrastructure | Tenant-scoped **`bc_*`** entities per [optimized domain model](./PRE_IMPLEMENTATION_OPTIMIZATION.md) + migration/rollback. Identity and snapshots are separate. |
| Authorization and tenant isolation | Existing `ai_operations.read` used to gate prototype | New granular `branding_dna.read/run/configure/approve` permissions and API-level checks, audit trail |
| Browser automation dependency | Playwright 1.56.1 in `apps/client` for QA; Python RRUGC Scout Manager/Scout v55 exists; Windows Scout device may be offline | **New independent branding-dna-worker** with separate pinned Playwright/runtime/profile/lease; reuse only Scout Manager patterns. Prefer dedicated authorized Windows host, not VPS Chrome fleet. See [worker ADR](./WORKER_ARCHITECTURE.md). |
| Shop discovery & listing crawl | Existing Agent-Reach fetches search/listing signals; does **not** enumerate all products/varieties | Platform-specific Amazon/Etsy/Shopify listing discovery adapters, consistent item identity, pagination, checkpoint, changed-listing detection and evidence manifest |
| Authenticated platform access | No universal access/authorization configured for this feature | Explicit account/shop authorization and allowed API scopes; public-only access may be restricted. Respect robots, platform ToS, CAPTCHA, rate limits and login barriers |
| Queue and workers | Existing CAM processing queue and worker services | Register `brand_shop_discover`, `brand_listing_extract`, `brand_image_analyze`, `brand_profile_aggregate` with retry, idempotency, pause/resume, heartbeat and bounded concurrency |
| Gemini | Existing Gemini provider/shared capacity supports image analysis | Typed JSON contracts for six profile layers, quota gates, job fairness, bounded backoff and reconciliation with evidence; benchmark quality/cost using approved sample shops |
| Image storage | Cloudflare R2/CDN integrated for CAM | Scoped storage paths, media hashing/dedupe, consent/licensing and retention, quotas and expiring links; do not persist unnecessary screenshots/cookies |
| Brand DNA | Conceptual 5+1 profiles in `creative-ai-hub`; no live profile DB | Evidence-backed field extraction, confidence and review flow; immutable approved versions and compare/rollback |
| Workflow integration | CAM skills, RRUGC generation and QA exist | Profile selection by `shop_id`/`profile_version`, version-locked output and style QA, explicit Apply action |
| Security | Existing server authentication, logging and policy gates | SSRF-resistant canonical URL allowlists, response IP/redirect validation, content size caps, no arbitrary local/network targets, secret management, agent mTLS/HMAC, per-tenant RBAC |
| Operational monitoring | CAM job logs and API readiness exist | Per-shop scan progress, last successful listing, retry reason, queue lag, Gemini use/cost, 429/CAPTCHA alert and operational kill switch |
| Test/rollout | UI prototype QA only | One authorized Amazon shop + one Etsy shop pilot, exhaustive item counts where access permits, data quality review, approvals and documented acceptance gates |

## Recommended implementation and additional checks

**Final worker recommendation: option C** from [WORKER_ARCHITECTURE.md](./WORKER_ARCHITECTURE.md): independent Windows scanner agent with a dedicated machine/VM, outward CAM API polling, own browser profile folders and Windows install/update channel. Do **not** run in the same Python Scout process or reuse Pinterest Chrome profile; the Windows Scout machine is not guaranteed online at every CAM deploy.

**Newly identified P0 gaps:** storefront canonicalization by seller ID/region; accessible listing completeness denominator; image-gallery evidence lineage; variant-aware image order; lease fencing + SQLite outbox; role/action-specific RBAC; explicit account eligibility and per-platform policies; human review of profile claims; tenant-isolated storage/credential scope; `Start Scan` readiness gate and stop switch. Source-specific API-first adapters preferred.

**P1:** content-hash incremental scan, perceptual duplicate detection, automatic parser-drift alerts, bounded Gemini budgets, compliant image retention, missing/tombstoned listings, visual QA and workload profiling.

Detailed [optimization decisions](./PRE_IMPLEMENTATION_OPTIMIZATION.md). Do not infer that adding a desktop UI makes browser crawling operational.

## External installs / connections

**No mandatory new package just to view the UI on main.** No install should be performed on production until worker architecture, access method and quotas are approved.

For a real pilot:

1. On a dedicated Windows runner: pinned Node LTS, pinned Playwright (separate worker lockfile), pinned matching Chromium, persistent browser profile directory with exclusive lock and OS-level account permissions. The version installed in CAM's QA client is **not** an operational Scanner installation.
2. Local Browser Agent binary/service plus secure CAM registration, heartbeat, encrypted token enrollment and opt-in shop access; no blind remote desktop or shared authenticated cookies.
3. Platform-specific access/API credentials if authorized/necessary. Amazon SP-API applies to approved seller data; public competitor storefront crawling does not automatically gain SP-API privileges. Etsy API access is subject to Etsy authorization and usage policies; Shopify public storefront access is limited and storefront/private APIs need scoped credentials.
4. Reuse CAM's existing PostgreSQL, R2 and Gemini pool; add DB migrations, worker job types, per-platform parsers, quota/rate limits, secrets and retention policies.
5. Calibrate Gemini prompts on sampled products (including image gallery order and multi-variant listings) before batch rollout. Cost and ETA depend on image count, real quotas and platform access; do not assume full 30–50 items per store are always retrievable.

## Blockers before enabling Start Scan

- [ ] Confirm specific shop URL(s), geography, data access/ownership rights, platform policies and crawl scope.
- [ ] Approve scanner runtime location (Windows runner recommended), browser-agent credentials and per-platform integration mode.
- [ ] Implement migrations and RBAC + API + scan workflow (no code exists yet for a production shop-scanning pipeline).
- [ ] Implement durable discovery/listing checkpoints, image evidence, retry on 403/429 without bypassing barriers, bounded processing.
- [ ] Add Gemini per-image/aggregate profile schema + quality gate and evidence provenance.
- [ ] Add preview-vs-real visual indicators, kill switch, logs/alerts and API/client contract tests.
- [ ] Pilot 2 authorized shops; verify source coverage and manual review before enabling bulk scanning.

**Status:** UI/docs versioned and testable; real scan **NOT READY**. No Playwright scanner, browser profiles, shop DB, backend endpoints or platform credentials have been installed for this feature.
